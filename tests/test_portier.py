"""End-to-end tests against a real Home Assistant core."""

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.portier.const import DEFAULTS, DOMAIN

BASE = {
    **DEFAULTS,
    "name": "Haustür",
    "trigger_entity": "binary_sensor.klingel",
    "camera_entity": "camera.tuer",
    "opener_entity": "input_boolean.oeffner",
    "contact_entity": None,
    "gong_entity": None,
    "debounce_seconds": 2.0,
    "pulse_seconds": 3.0,
}


async def _setup(hass: HomeAssistant, **options):
    await async_setup_component(hass, "input_boolean", {"input_boolean": {"oeffner": {}}})
    hass.states.async_set("binary_sensor.klingel", "off")
    hass.states.async_set("camera.tuer", "idle", {"access_token": "abc"})
    entry = MockConfigEntry(domain=DOMAIN, title="Haustür", options={**BASE, **options})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _press(hass):
    hass.states.async_set("binary_sensor.klingel", "on")
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.klingel", "off")
    await hass.async_block_till_done()


async def test_config_flow(hass: HomeAssistant) -> None:
    hass.states.async_set("binary_sensor.klingel", "off")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "form"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Haustür", "trigger_entity": "binary_sensor.klingel"},
    )
    assert result["step_id"] == "notify"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    assert result["options"]["trigger_entity"] == "binary_sensor.klingel"
    assert result["options"]["opener_entity"] is None
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    # Options flow with sections round-trips
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "name": "Vorne",
            "trigger_entity": "binary_sensor.klingel",
            "notifications": {"notify_cooldown": 10},
            "opener_options": {},
            "trigger_options": {},
            "live_view": {"live_seconds": 90},
        },
    )
    assert result["type"] == "create_entry"
    assert entry.options["live_seconds"] == 90
    assert entry.options["notify_cooldown"] == 10
    assert entry.title == "Vorne"


async def test_ring_edge_debounce_and_entities(hass: HomeAssistant) -> None:
    events = []
    hass.bus.async_listen("portier_ring", events.append)
    entry = await _setup(hass)
    await _press(hass)
    await _press(hass)  # within debounce -> ignored
    assert len(events) == 1
    door = entry.runtime_data
    assert door.state.ring_count == 1
    ev = [s for s in hass.states.async_all("event") if s.entity_id == "event.haustur_doorbell"]
    assert ev and ev[0].attributes["event_type"] == "ring"
    sensor = [s for s in hass.states.async_all("sensor") if s.entity_id == "sensor.haustur_last_ring"]
    assert sensor and sensor[0].state not in ("unknown", "unavailable")


async def test_event_entity_trigger_with_filter(hass: HomeAssistant) -> None:
    hass.states.async_set("event.shelly", "unknown", {"event_type": None})
    events = []
    hass.bus.async_listen("portier_ring", events.append)
    await _setup(hass, trigger_entity="event.shelly", event_types=["single_push"])
    hass.states.async_set("event.shelly", "2026-11-18T10:00:00", {"event_type": "long_push"})
    await hass.async_block_till_done()
    assert not events
    hass.states.async_set("event.shelly", "2026-11-18T10:00:05", {"event_type": "single_push"})
    await hass.async_block_till_done()
    assert len(events) == 1


async def test_relay_pulse_turns_off(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    await entry.runtime_data.async_open()
    await hass.async_block_till_done()
    assert hass.states.get("input_boolean.oeffner").state == "on"
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=4))
    await hass.async_block_till_done()
    assert hass.states.get("input_boolean.oeffner").state == "off"
    assert not entry.runtime_data._pending_off


async def test_device_pulse_mode_only_turns_on(hass: HomeAssistant) -> None:
    entry = await _setup(hass, pulse_mode="device")
    await entry.runtime_data.async_open()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=10))
    await hass.async_block_till_done()
    assert hass.states.get("input_boolean.oeffner").state == "on"


async def test_lock_uses_open_when_supported(hass: HomeAssistant) -> None:
    hass.states.async_set("lock.tuer", "locked", {"supported_features": 1})
    calls = async_mock_service(hass, "lock", "open")
    entry = await _setup(hass, opener_entity="lock.tuer")
    await entry.runtime_data.async_open()
    assert len(calls) == 1


async def test_restart_recovery_switches_off(hass: HomeAssistant, hass_storage) -> None:
    await async_setup_component(hass, "input_boolean", {"input_boolean": {"oeffner": {}}})
    await hass.services.async_call(
        "input_boolean", "turn_on", {"entity_id": "input_boolean.oeffner"}, blocking=True
    )
    entry = MockConfigEntry(domain=DOMAIN, title="Haustür", options=BASE)
    hass_storage[f"portier.{entry.entry_id}"] = {
        "version": 1,
        "key": f"portier.{entry.entry_id}",
        "data": {"pending_off": ["input_boolean.oeffner"]},
    }
    hass.states.async_set("binary_sensor.klingel", "off")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("input_boolean.oeffner").state == "off"


async def test_mobile_push_and_action(hass: HomeAssistant) -> None:
    calls = async_mock_service(hass, "notify", "mobile_app_pixel")
    entry = await _setup(hass, notify_services=["mobile_app_pixel"])
    await _press(hass)
    await hass.async_block_till_done()
    assert len(calls) == 1
    data = calls[0].data["data"]
    assert data["image"] == "/api/camera_proxy/camera.tuer"
    # Tapping the notification opens this door's live picture; no extra "Livebild" button.
    assert data["clickAction"] == data["url"] == f"/portier?show={entry.entry_id}"
    assert len(data["actions"]) == 1
    action = data["actions"][0]["action"]
    assert action.startswith("PORTIER_OPEN_")
    assert data["actions"][0]["authenticationRequired"] is True

    opened = []
    hass.bus.async_listen("portier_opened", opened.append)
    hass.bus.async_fire("mobile_app_notification_action", {"action": action})
    await hass.async_block_till_done()
    assert len(opened) == 1 and opened[0].data["via"] == "push"
    # Same button a second time does nothing
    hass.bus.async_fire("mobile_app_notification_action", {"action": action})
    await hass.async_block_till_done()
    assert len(opened) == 1
    # Fake token does nothing
    hass.bus.async_fire("mobile_app_notification_action", {"action": "PORTIER_OPEN_zzz"})
    await hass.async_block_till_done()
    assert len(opened) == 1
    assert entry.runtime_data.state.last_opened is not None


async def test_push_cooldown(hass: HomeAssistant) -> None:
    calls = async_mock_service(hass, "notify", "mobile_app_pixel")
    await _setup(hass, notify_services=["mobile_app_pixel"], debounce_seconds=0)
    await _press(hass)
    await _press(hass)
    await hass.async_block_till_done()
    assert len(calls) == 1


async def test_telegram_photo_and_callback(hass: HomeAssistant) -> None:
    photos = async_mock_service(hass, "telegram_bot", "send_photo")
    async_mock_service(hass, "telegram_bot", "send_message")
    answers = async_mock_service(hass, "telegram_bot", "answer_callback_query")
    await _setup(hass, telegram_targets="12345, -99")
    await _press(hass)
    await hass.async_block_till_done()
    assert len(photos) == 1
    sent = photos[0].data
    assert sent["chat_id"] == [12345, -99]
    assert "/api/camera_proxy/camera.tuer?token=abc" in sent["url"]
    label, command = sent["inline_keyboard"][0][0]
    assert command.startswith("/portier_open ")

    opened = []
    hass.bus.async_listen("portier_opened", opened.append)
    # Foreign chat is rejected
    hass.bus.async_fire("telegram_callback", {"data": command, "chat_id": 777, "id": "q0"})
    await hass.async_block_till_done()
    assert not opened
    hass.bus.async_fire(
        "telegram_callback", {"data": command, "chat_id": 12345, "id": "q1", "from_first": "Phil"}
    )
    await hass.async_block_till_done()
    assert len(opened) == 1
    assert answers[-1].data["message"].startswith("✅")


async def test_no_open_button_without_opener(hass: HomeAssistant) -> None:
    calls = async_mock_service(hass, "notify", "mobile_app_pixel")
    await _setup(hass, opener_entity=None, notify_services=["mobile_app_pixel"])
    await _press(hass)
    await hass.async_block_till_done()
    actions = calls[0].data["data"].get("actions", [])
    assert all(not a["action"].startswith("PORTIER_OPEN_") for a in actions)
    assert not [s for s in hass.states.async_all("button") if "open_door" in s.entity_id]


async def test_websocket_subscribe_and_open(hass: HomeAssistant, hass_ws_client) -> None:
    await async_setup_component(hass, "http", {})
    entry = await _setup(hass)
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "portier/subscribe"})
    assert (await client.receive_json())["success"]
    snap = (await client.receive_json())["event"]
    assert snap["type"] == "snapshot"
    assert snap["doors"][0]["name"] == "Haustür"
    assert snap["doors"][0]["can_open"] is True

    await _press(hass)
    msgs = [(await client.receive_json())["event"] for _ in range(2)]
    assert {m["type"] for m in msgs} == {"ring", "snapshot"}

    await client.send_json({"id": 2, "type": "portier/open", "entry_id": entry.entry_id})
    while True:
        msg = await client.receive_json()
        if msg.get("id") == 2 and msg["type"] == "result":
            break
    assert msg["success"]
    assert hass.states.get("input_boolean.oeffner").state == "on"


async def test_unload_switches_off(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    await entry.runtime_data.async_open()
    assert hass.states.get("input_boolean.oeffner").state == "on"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("input_boolean.oeffner").state == "off"



async def test_panel_create_edit_delete(hass: HomeAssistant, hass_ws_client) -> None:
    await async_setup_component(hass, "http", {})
    entry = await _setup(hass)
    hass.states.async_set("switch.hinten", "off")
    hass.states.async_set("binary_sensor.hinten", "off")
    client = await hass_ws_client(hass)

    await client.send_json({"id": 1, "type": "portier/config"})
    cfg = (await client.receive_json())["result"]
    assert cfg["doors"][entry.entry_id]["name"] == "Haustür"

    # invalid: wrong domain for camera
    await client.send_json({"id": 2, "type": "portier/door/save",
        "options": {"name": "Hinten", "trigger_entity": "binary_sensor.hinten", "camera_entity": "switch.hinten"}})
    msg = await client.receive_json()
    assert not msg["success"] and "camera_entity" in msg["error"]["message"]

    # invalid: opener already used by the first door
    await client.send_json({"id": 3, "type": "portier/door/save",
        "options": {"name": "Hinten", "trigger_entity": "binary_sensor.hinten", "opener_entity": "input_boolean.oeffner"}})
    msg = await client.receive_json()
    assert not msg["success"] and "opener_in_use" in msg["error"]["message"]

    # create
    await client.send_json({"id": 4, "type": "portier/door/save",
        "options": {"name": "Hinten", "trigger_entity": "binary_sensor.hinten", "opener_entity": "switch.hinten", "sound": "soft"}})
    msg = await client.receive_json()
    assert msg["success"], msg
    new_id = msg["result"]["entry_id"]
    await hass.async_block_till_done()
    new = hass.config_entries.async_get_entry(new_id)
    assert new.title == "Hinten" and new.options["sound"] == "soft"
    assert new.options["live_seconds"] == 60

    # edit existing: rename + clear camera + change volume
    await client.send_json({"id": 5, "type": "portier/door/save", "entry_id": entry.entry_id,
        "options": {"name": "Vorne", "trigger_entity": "binary_sensor.klingel", "camera_entity": "", "opener_entity": "input_boolean.oeffner", "volume": 30}})
    msg = await client.receive_json()
    assert msg["success"], msg
    await hass.async_block_till_done()
    assert entry.title == "Vorne" and entry.options["camera_entity"] is None and entry.options["volume"] == 30
    assert entry.runtime_data.name == "Vorne"

    # delete
    await client.send_json({"id": 6, "type": "portier/door/delete", "entry_id": new_id})
    assert (await client.receive_json())["success"]
    await hass.async_block_till_done()
    assert hass.config_entries.async_get_entry(new_id) is None


async def test_panel_edit_requires_admin(hass: HomeAssistant, hass_ws_client, hass_read_only_access_token) -> None:
    await async_setup_component(hass, "http", {})
    await _setup(hass)
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json({"id": 1, "type": "portier/config"})
    assert not (await client.receive_json())["success"]


async def test_two_doors_keep_separate_notify(hass: HomeAssistant, hass_ws_client) -> None:
    await async_setup_component(hass, "http", {})
    async_mock_service(hass, "notify", "mobile_app_handy")
    first = await _setup(hass)
    hass.states.async_set("binary_sensor.hinten", "off")
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "portier/door/save",
        "options": {"name": "Hinten", "trigger_entity": "binary_sensor.hinten"}})
    second_id = (await client.receive_json())["result"]["entry_id"]
    await hass.async_block_till_done()
    await client.send_json({"id": 2, "type": "portier/config"})
    cfg = (await client.receive_json())["result"]
    opts = dict(cfg["doors"][second_id])
    opts["notify_services"] = ["mobile_app_handy"]
    await client.send_json({"id": 3, "type": "portier/door/save", "entry_id": second_id, "options": opts})
    assert (await client.receive_json())["success"]
    await hass.async_block_till_done()
    await client.send_json({"id": 4, "type": "portier/config"})
    cfg = (await client.receive_json())["result"]
    assert cfg["doors"][second_id]["notify_services"] == ["mobile_app_handy"]
    assert cfg["doors"][first.entry_id]["notify_services"] == []


async def test_ring_still_works_after_editing_push(hass: HomeAssistant, hass_ws_client) -> None:
    await async_setup_component(hass, "http", {})
    notify = async_mock_service(hass, "notify", "mobile_app_handy")
    entry = await _setup(hass)
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "portier/config"})
    opts = dict((await client.receive_json())["result"]["doors"][entry.entry_id])
    opts.update(notify_services=["mobile_app_handy"], telegram_targets="", notify_cooldown=0)
    await client.send_json({"id": 2, "type": "portier/door/save", "entry_id": entry.entry_id, "options": opts})
    assert (await client.receive_json())["success"]
    await hass.async_block_till_done()
    rings = []
    hass.bus.async_listen("portier_ring", rings.append)
    await _press(hass)
    await hass.async_block_till_done()
    assert len(rings) == 1
    assert len(notify) == 1


async def test_dismiss_closes_ring_everywhere(hass: HomeAssistant, hass_ws_client) -> None:
    await async_setup_component(hass, "http", {})
    entry = await _setup(hass)
    a = await hass_ws_client(hass)
    b = await hass_ws_client(hass)
    for c in (a, b):
        await c.send_json({"id": 1, "type": "portier/subscribe"})
        await c.receive_json(); await c.receive_json()
    await _press(hass)
    ring_time = None
    for c in (a, b):
        msgs = [(await c.receive_json())["event"] for _ in range(2)]
        ring_time = next(m["time"] for m in msgs if m["type"] == "ring")
    # stale ring time is ignored
    await a.send_json({"id": 2, "type": "portier/dismiss", "entry_id": entry.entry_id, "ring_time": "2000-01-01T00:00:00+00:00"})
    assert (await a.receive_json())["result"]["closed"] is False
    await a.send_json({"id": 3, "type": "portier/dismiss", "entry_id": entry.entry_id, "ring_time": ring_time})
    got_a = await a.receive_json()
    got_b = (await b.receive_json())["event"]
    assert got_b == {"type": "dismiss", "entry_id": entry.entry_id, "ring_time": ring_time}


async def test_loader_file_endpoint_and_repair(hass: HomeAssistant, hass_client) -> None:
    from pathlib import Path
    from homeassistant.helpers import issue_registry as ir
    from homeassistant.components import frontend
    from custom_components.portier.frontend import LOADER_URL, _async_check_loader

    await async_setup_component(hass, "http", {})
    await _setup(hass)
    loader = Path(hass.config.path("www", "portier", "loader.js"))
    assert loader.exists() and "/portier_static/current" in loader.read_text()
    client = await hass_client()
    r = await client.get("/portier_static/current")
    assert r.status == 200
    assert (await r.json())["url"].startswith("/portier_static/portier.js?v=")
    # without the YAML line a repair notice is shown ...
    reg = ir.async_get(hass)
    assert reg.async_get_issue("portier", "loader_missing") is not None
    # ... and it disappears once the loader is configured
    frontend.add_extra_js_url(hass, LOADER_URL)
    _async_check_loader(hass)
    assert reg.async_get_issue("portier", "loader_missing") is None


async def test_repair_flow_writes_yaml(hass: HomeAssistant, hass_client, tmp_path, monkeypatch) -> None:
    from homeassistant.components.repairs import DOMAIN as REPAIRS
    from homeassistant.helpers import issue_registry as ir

    cfg = tmp_path / "configuration.yaml"
    cfg.write_text("default_config:\n")
    real_path = hass.config.path
    monkeypatch.setattr(hass.config, "path", lambda *p: str(cfg) if p == ("configuration.yaml",) else real_path(*p))
    await async_setup_component(hass, "http", {})
    await async_setup_component(hass, REPAIRS, {})
    await _setup(hass)
    assert ir.async_get(hass).async_get_issue("portier", "loader_missing").is_fixable
    client = await hass_client()
    r = await client.post("/api/repairs/issues/fix", json={"handler": "portier", "issue_id": "loader_missing"})
    flow = await r.json()
    assert flow["step_id"] == "confirm"
    restarts = async_mock_service(hass, "homeassistant", "restart")
    r = await client.post(f"/api/repairs/issues/fix/{flow['flow_id']}", json={})
    flow = await r.json()
    assert flow["step_id"] == "restart"
    assert "/local/portier/loader.js" in cfg.read_text()
    reg = ir.async_get(hass)
    # Written, but not active yet: the notice changes to "restart", it does not vanish.
    assert reg.async_get_issue("portier", "loader_missing") is None
    assert reg.async_get_issue("portier", "loader_restart") is not None
    r = await client.post(f"/api/repairs/issues/fix/{flow['flow_id']}", json={})
    assert (await r.json())["type"] == "create_entry"
    await hass.async_block_till_done()
    assert len(restarts) == 1


async def test_restart_notice_leads_straight_to_restart(hass: HomeAssistant, hass_client) -> None:
    from homeassistant.components.repairs import DOMAIN as REPAIRS

    from custom_components.portier.frontend import async_loader_written

    await async_setup_component(hass, "http", {})
    await async_setup_component(hass, REPAIRS, {})
    await _setup(hass)
    async_loader_written(hass)
    restarts = async_mock_service(hass, "homeassistant", "restart")
    client = await hass_client()
    r = await client.post("/api/repairs/issues/fix", json={"handler": "portier", "issue_id": "loader_restart"})
    flow = await r.json()
    assert flow["step_id"] == "restart"
    r = await client.post(f"/api/repairs/issues/fix/{flow['flow_id']}", json={})
    assert (await r.json())["type"] == "create_entry"
    await hass.async_block_till_done()
    assert len(restarts) == 1


async def test_talk_option(hass: HomeAssistant) -> None:
    """Two-way audio is off unless switched on, and reaches the frontend snapshot."""
    entry = await _setup(hass)
    assert entry.runtime_data.snapshot(True)["talk"] is False
    entry2 = await _setup(hass, talk=True, opener_entity=None, trigger_entity="binary_sensor.klingel2")
    assert entry2.runtime_data.snapshot(True)["talk"] is True


async def _telegram_ring(hass: HomeAssistant):
    from homeassistant.core import SupportsResponse

    async_mock_service(
        hass,
        "telegram_bot",
        "send_photo",
        response={"chats": [{"chat_id": 12345, "message_id": 7}]},
        supports_response=SupportsResponse.OPTIONAL,
    )
    async_mock_service(hass, "telegram_bot", "send_message")
    edits = async_mock_service(hass, "telegram_bot", "edit_caption")
    entry = await _setup(hass, telegram_targets="12345", action_window_seconds=120)
    await _press(hass)
    await hass.async_block_till_done()
    return entry, edits


async def test_telegram_shows_opened(hass: HomeAssistant) -> None:
    entry, edits = await _telegram_ring(hass)
    await entry.runtime_data.async_open(via="app")
    await hass.async_block_till_done()
    assert len(edits) == 1
    assert edits[0].data["message_id"] == 7
    assert edits[0].data["chat_id"] == 12345
    assert edits[0].data["inline_keyboard"] == []
    assert "✅ Tür geöffnet" in edits[0].data["caption"]
    # Opening again later does not touch the message a second time.
    await entry.runtime_data.async_open(via="app")
    await hass.async_block_till_done()
    assert len(edits) == 1


async def test_telegram_button_expires(hass: HomeAssistant) -> None:
    _entry, edits = await _telegram_ring(hass)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=125))
    await hass.async_block_till_done()
    assert len(edits) == 1
    assert edits[0].data["inline_keyboard"] == []
    assert "nicht mehr möglich" in edits[0].data["caption"]


async def test_migrate_window_minutes_to_seconds(hass: HomeAssistant) -> None:
    opts = {k: v for k, v in BASE.items() if k != "action_window_seconds"}
    opts["action_window_minutes"] = 5
    entry = MockConfigEntry(domain=DOMAIN, title="Haustür", options=opts, version=1, minor_version=1)
    entry.add_to_hass(hass)
    await async_setup_component(hass, "input_boolean", {"input_boolean": {"oeffner": {}}})
    hass.states.async_set("binary_sensor.klingel", "off")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.minor_version == 2
    assert entry.options["action_window_seconds"] == 120
    assert "action_window_minutes" not in entry.options


async def test_config_flow_telegram_picker(hass: HomeAssistant) -> None:
    hass.states.async_set("binary_sensor.klingel", "off")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Haustür", "trigger_entity": "binary_sensor.klingel"}
    )
    assert result["step_id"] == "notify"
    # The Telegram field is a multi-select, not a text box.
    field = next(k for k in result["data_schema"].schema if k == "telegram_targets")
    selector = result["data_schema"].schema[field]
    assert selector.config["multiple"] is True
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"telegram_targets": ["12345", " -99", "kaputt"]}
    )
    assert result["type"] == "create_entry"
    assert result["options"]["telegram_targets"] == "12345, -99"
    # Loader not active yet: the finish dialog says what is still missing.
    assert result["description"] == "loader_pending"


async def test_removing_last_door_removes_loader(hass: HomeAssistant, tmp_path, monkeypatch) -> None:
    from pathlib import Path

    from homeassistant.helpers import issue_registry as ir

    cfg = tmp_path / "configuration.yaml"
    cfg.write_text("default_config:\nfrontend:\n  extra_module_url:\n    - /local/portier/loader.js\n")
    real_path = hass.config.path
    monkeypatch.setattr(hass.config, "path", lambda *p: str(cfg) if p == ("configuration.yaml",) else real_path(*p))
    first = await _setup(hass)
    second = MockConfigEntry(domain=DOMAIN, title="Garten", options={**BASE, "name": "Garten"})
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()
    loader = Path(hass.config.path("www", "portier", "loader.js"))
    assert loader.exists()

    # One door left: nothing is touched.
    await hass.config_entries.async_remove(first.entry_id)
    await hass.async_block_till_done()
    assert "/local/portier/loader.js" in cfg.read_text()

    # Last door gone: line and loader file are removed again.
    await hass.config_entries.async_remove(second.entry_id)
    await hass.async_block_till_done()
    assert "/local/portier/loader.js" not in cfg.read_text()
    assert not loader.exists()

    # A new door right away (no restart) gets the repair notice again.
    await _setup(hass)
    assert ir.async_get(hass).async_get_issue("portier", "loader_missing") is not None

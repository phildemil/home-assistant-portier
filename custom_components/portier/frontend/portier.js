/*
 * Portier – Türklingel-Overlay und Seitenleiste für Home Assistant.
 *
 * Läuft auf jeder HA-Seite (über frontend.add_extra_js_url). Ein Controller pro
 * Browser-Tab abonniert `portier/subscribe`. Klingelt es, schwebt über der
 * aktuellen Ansicht eine Türstation mit Livebild, Countdown und Öffnen-Taster.
 */

const VERSION = "1.18.5";
const LS_DEVICE = "portier.device";
const LS_PLAYED = "portier.played.";
const LATE_MS = 5000; // rings reaching this page later than this get no live picture
/** Same value as NOTIFY_ALL_MOBILE in const.py (old "all phones" choice, no longer offered). */
const ALL_PHONES = "all_mobile_apps";

/* ------------------------------------------------------------------ helpers */

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const deviceSettings = () => {
  try {
    return { overlay: true, sound: true, ...JSON.parse(localStorage.getItem(LS_DEVICE) || "{}") };
  } catch {
    return { overlay: true, sound: true };
  }
};
const saveDeviceSettings = (s) => {
  try {
    localStorage.setItem(LS_DEVICE, JSON.stringify(s));
  } catch {
    /* private mode */
  }
};

const timeOf = (iso) =>
  iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "–";

function relative(iso, now = Date.now()) {
  if (!iso) return "noch nie";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 10) return "gerade eben";
  if (s < 60) return `vor ${s} s`;
  const m = Math.round(s / 60);
  if (m < 60) return `vor ${m} min`;
  const d = new Date(iso);
  const today = new Date(now).toDateString() === d.toDateString();
  return today ? `heute ${timeOf(iso)}` : d.toLocaleDateString([], { day: "numeric", month: "short" }) + ` ${timeOf(iso)}`;
}

const rootHass = () => document.querySelector("home-assistant")?.hass;

/* ------------------------------------------------------------ accent colors */

// The two accent colors (brass = buttons, lamp, line; green = confirmations), one
// setting for all devices. Standard keeps the colors of the CSS below.
const LOOK_DEFAULT = { mode: "default", accent: "#C9A44C", accent2: "#4FA38A" };
const LOOK_VARS = [
  "--pt-brass", "--pt-brass-hi", "--pt-brass-lo", "--pt-on-brass",
  "--pt-verdigris", "--pt-verdigris-hi", "--pt-verdigris-lo", "--pt-on-verdigris",
];
let colorCtx;
/** Any CSS color -> "#rrggbb" (null if it is not a color). */
function toHex(color) {
  if (!color) return null;
  colorCtx ??= document.createElement("canvas").getContext("2d");
  colorCtx.fillStyle = "#010203";
  colorCtx.fillStyle = color;
  const out = colorCtx.fillStyle;
  return /^#[0-9a-f]{6}$/i.test(out) && (out !== "#010203" || /^#010203$/i.test(color)) ? out : null;
}
const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
const mix = (hex, other, t) =>
  "#" + rgb(hex).map((c, i) => Math.round(c + (rgb(other)[i] - c) * t).toString(16).padStart(2, "0")).join("");
/** Dark or light text, whichever reads better on this color. */
function onColor(hex) {
  const [r, g, b] = rgb(hex).map((c) => {
    c /= 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return lum > 0.32 ? mix(hex, "#000000", 0.8) : "#ffffff";
}
const themeColor = (name) => toHex(getComputedStyle(document.documentElement).getPropertyValue(name).trim());
/** CSS variables for a look, or null for Portier's standard colors. */
function lookVars(look) {
  if (!look || look.mode === "default") return null;
  const system = look.mode === "system";
  const a = (system ? themeColor("--primary-color") : toHex(look.accent)) || LOOK_DEFAULT.accent;
  const b = (system ? themeColor("--accent-color") : toHex(look.accent2)) || LOOK_DEFAULT.accent2;
  return {
    "--pt-brass": a,
    "--pt-brass-hi": mix(a, "#ffffff", 0.3),
    "--pt-brass-lo": mix(a, "#000000", 0.25),
    "--pt-on-brass": onColor(a),
    "--pt-verdigris": b,
    "--pt-verdigris-hi": mix(b, "#ffffff", 0.35),
    "--pt-verdigris-lo": mix(b, "#000000", 0.35),
    "--pt-on-verdigris": onColor(b),
  };
}
/** Put the colors on a Portier element (overlay or panel); its parts inherit them. */
function applyLook(el, look) {
  if (!el) return;
  const vars = lookVars(look);
  for (const name of LOOK_VARS) {
    if (vars) el.style.setProperty(name, vars[name]);
    else el.style.removeProperty(name);
  }
}

/** The Home Assistant app exposes a bridge to its web view; a plain browser does not. */
function isHaApp() {
  const bridge = window.externalApp || window.externalAppV2 || window.webkit?.messageHandlers?.externalBus;
  return !!(bridge || rootHass()?.auth?.external);
}

/* -------------------------------------------------------------------- sound */

class Chime {
  constructor() {
    this.ctx = null;
    const unlock = () => this.unlock();
    // The first tap or key press anywhere in HA unlocks audio for this tab.
    for (const ev of ["pointerdown", "keydown", "touchstart"]) {
      window.addEventListener(ev, unlock, { capture: true, passive: true });
    }
  }

  get ready() {
    return this.ctx?.state === "running";
  }

  unlock() {
    try {
      this.ctx ??= new (window.AudioContext || window.webkitAudioContext)();
      if (this.ctx.state === "suspended") this.ctx.resume();
    } catch {
      /* no audio */
    }
  }

  /** For buttons: wake the audio first (a fresh page starts it suspended), then play. */
  async playNow(kind = "dingdong", volume = 70) {
    this.unlock();
    try {
      await Promise.race([this.ctx?.resume(), new Promise((r) => setTimeout(r, 500))]);
    } catch {
      /* blocked */
    }
    return this.play(kind, volume);
  }

  /** Two (or three) soft bell partials. Returns false if the browser blocks audio. */
  play(kind = "dingdong", volume = 70) {
    if (kind === "off") return true;
    this.unlock();
    if (!this.ready) return false;
    const notes = {
      dingdong: [[659.3, 0], [523.3, 0.55]],
      soft: [[587.3, 0], [440, 0.7]],
      triple: [[523.3, 0], [659.3, 0.32], [784, 0.64]],
    }[kind] || [[659.3, 0], [523.3, 0.55]];
    const t0 = this.ctx.currentTime + 0.02;
    const master = this.ctx.createGain();
    master.gain.value = Math.max(0, Math.min(1, volume / 100)) * 0.5;
    master.connect(this.ctx.destination);
    for (const [freq, at] of notes) {
      for (const [mult, amp] of [[1, 1], [2.76, 0.25], [5.4, 0.08]]) {
        const osc = this.ctx.createOscillator();
        const g = this.ctx.createGain();
        osc.type = "sine";
        osc.frequency.value = freq * mult;
        g.gain.setValueAtTime(0, t0 + at);
        g.gain.linearRampToValueAtTime(amp, t0 + at + 0.01);
        g.gain.exponentialRampToValueAtTime(0.0001, t0 + at + 1.6 / mult ** 0.3);
        osc.connect(g).connect(master);
        osc.start(t0 + at);
        osc.stop(t0 + at + 1.8);
      }
    }
    return true;
  }
}

/** Where a door opening came from, for the opener history. */
const OPEN_VIA = {
  app: "Home Assistant",
  ha_app: "Home-Assistant-App",
  browser: "Browser",
  push: "Push-Nachricht",
  nfc: "NFC an der Tür",
  telegram: "Telegram",
  button: "Taste in Home Assistant",
  service: "Aktion portier.open",
};
/** "Heute 17:16:27", "Gestern 14:56:54", "Fr 20.11. 12:59:32" (year added if not this year). */
function when(iso) {
  const d = new Date(iso);
  const time = d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((day(new Date()) - day(d)) / 86400000);
  if (days === 0) return `Heute ${time}`;
  if (days === 1) return `Gestern ${time}`;
  const wd = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"][d.getDay()];
  const dd = `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}.`;
  const yy = d.getFullYear() === new Date().getFullYear() ? "" : String(d.getFullYear());
  return `${wd} ${dd}${yy} ${time}`;
}

/** Which button rang (its name, entity id as tooltip) or who pressed "Testklingel". */
const ringWho = (r) =>
  r.source === "test"
    ? [r.who || "Automation", "Testklingel"]
    : [r.who || r.entity || "Klingeltaster", r.who && r.entity ? r.entity : "Klingeltaster"];
/** One history row as aligned columns: time · way · name (name on the right). */
const histRow = (time, name, detail) =>
  `<li><time>${esc(time)}</time><span title="${esc(detail)}">${esc(detail)}</span><b>${esc(name)}</b></li>`;
/** " · von phil über Telegram" for the newest opening, if the history has it. */
function openedBy(door) {
  const r = door.opens?.[0];
  if (!r || (door.last_opened && r.time !== door.last_opened)) return "";
  // The live view keeps it short: push button and app are both just "the app" there.
  // The opener history shows exactly which way it was.
  const how = ["push", "ha_app"].includes(r.via) ? "App" : OPEN_VIA[r.via] || r.via;
  return ` · von ${esc(openWho(r))} über ${esc(how)}`;
}
const openWho = (r) => r.who || (["button", "service"].includes(r.via) ? "Automation" : "Unbekannt");

/** A "play sound" button that says what it is doing, like the test ring button. */
async function soundButton(btn, kind, volume) {
  if (btn.disabled) return false;
  const label = btn.textContent;
  btn.disabled = true;
  btn.classList.add("working");
  btn.textContent = "♪ Spielt …";
  const ok = await ctrl.chime.playNow(kind === "off" ? "dingdong" : kind, volume);
  if (!ok) {
    btn.classList.replace("working", "failed");
    btn.textContent = "Ton blockiert";
  }
  setTimeout(() => {
    btn.disabled = false;
    btn.classList.remove("working", "failed");
    btn.textContent = label;
  }, ok ? 1800 : 2500);
  return ok;
}

/* ------------------------------------------------------------------- camera */

/**
 * Live camera straight from Home Assistant's camera API:
 * a snapshot appears immediately, then WebRTC (go2rtc, Nest, Reolink, …),
 * else HLS where the browser plays it natively, else MJPEG – which every
 * HA camera provides. Nothing is fetched from outside Home Assistant.
 */
class CameraView {
  /**
   * @param {object} [opts]
   * @param {boolean} [opts.talk] offer a microphone back channel (two-way audio)
   * @param {(state: string) => void} [opts.onTalk] "wait" | "ready" | "unsupported" | "insecure"
   */
  constructor(entityId, getHass, { talk = false, onTalk = null, entryId = null } = {}) {
    this.entityId = entityId;
    this.getHass = getHass;
    this.talk = talk;
    this.onTalk = onTalk;
    this.audioSender = null;
    this.micStream = null;
    this.el = document.createElement("div");
    this.el.className = "cam";
    this.still = document.createElement("img");
    this.still.className = "cam-still";
    this.still.alt = "";
    // Home Assistant's still failed (some cameras): ask Portier for one, once.
    this.still.addEventListener("error", async () => {
      if (!entryId || this.closed || this.still.src.startsWith("data:")) return;
      const url = await ctrl.portierStill(entryId);
      if (url && !this.closed) this.still.src = url;
    });
    this.el.append(this.still);
    this.media = null;
    this.pending = new Set(); // videos started but not shown yet
    this.gen = 0;
    this.retries = 0;
    // In a background tab a live video stalls or falls behind; coming back fixes that.
    this.onVisible = () => {
      if (document.visibilityState === "hidden") {
        this.hiddenAt ||= Date.now();
        return;
      }
      const away = this.hiddenAt ? Date.now() - this.hiddenAt : 0;
      this.hiddenAt = 0;
      if (!this.closed && away >= 3000) this.resume();
    };
    document.addEventListener("visibilitychange", this.onVisible);
    this.watch = setInterval(() => this.checkAlive(), 2000);
    this.pc = null;
    this.unsub = null;
    this.closed = false;
    this.mode = "";
  }

  get hass() {
    return this.getHass();
  }

  setTalk(state) {
    this.talkState = state;
    this.onTalk?.(state);
  }

  async start() {
    const gen = ++this.gen; // a restart makes everything still running from before stale
    this.starting = true;
    this.startedAt = Date.now();
    const hass = this.hass;
    const state = hass?.states?.[this.entityId];
    if (this.talk) {
      // Browsers hand out the microphone only on https:// (or localhost).
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
        this.talk = false;
        this.setTalk("insecure");
      } else {
        this.setTalk("wait");
      }
    }
    if (!hass || !state) {
      if (this.talk) this.setTalk("unsupported");
      this.starting = false;
      return this.message("Keine Kamera");
    }
    const pic = state.attributes.entity_picture;
    if (pic) this.still.src = `${pic}${pic.includes("?") ? "&" : "?"}t=${Date.now()}`;
    let types = [];
    try {
      types = (await hass.callWS({ type: "camera/capabilities", entity_id: this.entityId })).frontend_stream_types || [];
    } catch {
      /* old HA: just try MJPEG */
    }
    if (this.closed || gen !== this.gen) return;
    // A ring must show a picture at once: WebRTC and HLS start together, the first one
    // with real frames is shown. WebRTC (almost no delay, needed for talking) takes over
    // if it arrives later; the loser is closed.
    const tries = [];
    if (types.includes("web_rtc") && window.RTCPeerConnection) {
      tries.push(
        this.webrtc(hass).then(
          (v) => this.offer(v, "webrtc", gen),
          (err) => {
            console.info("Portier: WebRTC nicht möglich", err?.message || err);
            if (gen === this.gen) this.stopWebrtc();
          },
        ),
      );
    }
    if (types.includes("hls") && document.createElement("video").canPlayType("application/vnd.apple.mpegurl")) {
      tries.push(
        this.hls(hass).then(
          (v) => this.offer(v, "hls", gen),
          (err) => console.info("Portier: HLS nicht möglich", err?.message || err),
        ),
      );
    }
    await Promise.all(tries);
    if (this.closed || gen !== this.gen) return;
    // Talking needs WebRTC; the other ways only receive.
    if (this.talk && this.mode !== "webrtc") this.setTalk("unsupported");
    if (!this.mode) await this.mjpeg(hass);
    if (gen === this.gen) this.starting = false;
  }

  /** A way delivered frames: show it, unless a better one is already on screen. */
  offer(video, mode, gen) {
    if (this.closed || gen !== this.gen || this.mode === "webrtc") return this.drop(video);
    this.retries = 0;
    video.classList.remove("cam-pending");
    this.swap(video, mode);
    this.el.classList.add("live");
  }

  /** Throw away a video element that is not (or no longer) shown. */
  drop(video) {
    if (!video) return;
    video.srcObject?.getTracks?.().forEach((t) => t.stop());
    video.srcObject = null;
    video.removeAttribute("src");
    video.load?.();
    video.remove();
  }

  /**
   * Start a hidden video and resolve once it really plays – a stream that never
   * delivers frames must not leave a black picture.
   */
  playing(attach, ms) {
    return new Promise((resolve, reject) => {
      const v = document.createElement("video");
      v.className = "cam-media cam-pending";
      v.muted = true;
      v.autoplay = true;
      v.playsInline = true;
      v.setAttribute("playsinline", "");
      const done = (err) => {
        clearTimeout(timer);
        if (err) {
          this.drop(v);
          reject(err);
        } else resolve(v);
      };
      const timer = setTimeout(() => done(new Error("kein Bild")), ms);
      v.addEventListener("playing", () => done(), { once: true });
      v.addEventListener("error", () => done(new Error("Videofehler")), { once: true });
      attach(v);
      this.el.append(v);
      v.play?.().catch(() => {});
      this.pending.add(v);
      v.addEventListener("emptied", () => this.pending.delete(v), { once: true });
    });
  }

  async hls(hass) {
    // A stream that is just starting (e.g. right after a restart) has no segments yet
    // and the player gives up – so try again for a while instead of staying black.
    const until = Date.now() + 20000;
    for (;;) {
      const { url } = await hass.callWS({ type: "camera/stream", entity_id: this.entityId, format: "hls" });
      if (this.closed) throw new Error("beendet");
      try {
        return await this.playing((v) => (v.src = url), Math.max(2000, until - Date.now()));
      } catch (err) {
        if (this.closed || this.mode === "webrtc" || Date.now() > until - 2000) throw err;
        await new Promise((r) => setTimeout(r, 1500));
      }
    }
  }

  webrtc(hass) {
    return new Promise(async (resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Zeitüberschreitung")), 12000);
      const fail = (err) => {
        clearTimeout(timer);
        reject(err);
      };
      try {
        let config = {};
        try {
          const cc = await hass.callWS({ type: "camera/webrtc/get_client_config", entity_id: this.entityId });
          config = cc.configuration || {};
        } catch {
          /* defaults */
        }
        const pc = new RTCPeerConnection(config);
        this.pc = pc;
        pc.addTransceiver("video", { direction: "recvonly" });
        // With talking enabled, audio goes both ways. The microphone is attached later
        // (replaceTrack, no renegotiation) and only while the talk button is held.
        const audio = pc.addTransceiver("audio", { direction: this.talk ? "sendrecv" : "recvonly" });
        this.audioSender = this.talk ? audio.sender : null;
        const stream = new MediaStream();
        pc.ontrack = (ev) => {
          stream.addTrack(ev.track);
          if (ev.track.kind === "video") {
            // A track alone is no picture yet: wait for real frames (inside the timeout).
            this.playing((v) => (v.srcObject = stream), 12000).then((v) => {
              clearTimeout(timer);
              if (this.pc === pc) resolve(v);
              else {
                this.drop(v);
                reject(new Error("beendet"));
              }
            }, fail);
          }
        };
        pc.onconnectionstatechange = () => {
          if (pc.connectionState === "failed") fail(new Error("Verbindung fehlgeschlagen"));
        };
        let sessionId = null;
        const pending = [];
        const sendCandidate = (c) =>
          hass.callWS({ type: "camera/webrtc/candidate", entity_id: this.entityId, session_id: sessionId, candidate: c }).catch(() => {});
        pc.onicecandidate = (ev) => {
          if (!ev.candidate) return;
          const c = ev.candidate.toJSON();
          sessionId ? sendCandidate(c) : pending.push(c);
        };
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        this.unsub = await hass.connection.subscribeMessage(
          async (msg) => {
            if (msg.type === "session") {
              sessionId = msg.session_id;
              pending.splice(0).forEach(sendCandidate);
            } else if (msg.type === "answer") {
              await pc.setRemoteDescription({ type: "answer", sdp: msg.answer });
              if (this.talk) {
                const ok = answerAcceptsAudio(msg.answer);
                if (!ok) this.audioSender = null;
                this.setTalk(ok ? "ready" : "unsupported");
              }
            } else if (msg.type === "candidate") {
              try {
                await pc.addIceCandidate(msg.candidate);
              } catch {
                /* ignore late candidates */
              }
            } else if (msg.type === "error") {
              fail(new Error(msg.message || msg.code));
            }
          },
          { type: "camera/webrtc/offer", entity_id: this.entityId, offer: offer.sdp },
        );
      } catch (err) {
        fail(err);
      }
    });
  }

  /** Camera sound on/off (videos start muted so they may autoplay). Returns the new state. */
  toggleSound(on) {
    const v = this.media?.tagName === "VIDEO" ? this.media : null;
    if (!v) return false;
    v.muted = on === undefined ? !v.muted : !on;
    v.play?.().catch(() => {});
    return !v.muted;
  }

  /** Start sending the microphone to the camera. Throws with a short German reason. */
  async talkStart() {
    if (!this.audioSender) throw new Error(this.talkState === "insecure" ? "Nur mit HTTPS" : "Kamera hört nicht");
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (err) {
      throw new Error(err?.name === "NotAllowedError" ? "Mikrofon nicht erlaubt" : "Kein Mikrofon");
    }
    if (this.closed || !this.audioSender) {
      stream.getTracks().forEach((t) => t.stop());
      throw new Error("Livebild beendet");
    }
    this.talkStop();
    this.micStream = stream;
    await this.audioSender.replaceTrack(stream.getAudioTracks()[0]);
    this.toggleSound(true); // hear the answer
  }

  talkStop() {
    this.audioSender?.replaceTrack(null).catch(() => {});
    this.micStream?.getTracks().forEach((t) => t.stop());
    this.micStream = null;
  }

  stopWebrtc() {
    this.talkStop();
    this.audioSender = null;
    try {
      this.unsub?.();
    } catch {
      /* already gone */
    }
    this.unsub = null;
    this.pc?.close();
    this.pc = null;
  }

  async mjpeg(hass) {
    try {
      const { path } = await hass.callWS({ type: "auth/sign_path", path: `/api/camera_proxy_stream/${this.entityId}` });
      if (this.closed) return;
      const img = document.createElement("img");
      img.alt = "";
      img.addEventListener("load", () => this.el.classList.add("live"), { once: true });
      img.src = path;
      this.swap(img, "mjpeg");
    } catch {
      /* the still image stays visible */
    }
  }

  message(text) {
    const p = document.createElement("div");
    p.className = "cam-empty";
    p.textContent = text;
    this.swap(p, "none");
  }

  swap(node, mode) {
    const old = this.media;
    if (old?.tagName === "IMG") old.src = "";
    if (old?.tagName === "VIDEO") {
      // Keep the sound setting when WebRTC takes over from HLS.
      if (node.tagName === "VIDEO") node.muted = old.muted;
      this.drop(old);
    }
    old?.remove();
    this.pending.delete(node);
    this.media = node;
    node.classList.add("cam-media");
    this.mode = mode;
    this.el.append(node);
  }

  /**
   * Watchdog while the picture is on screen: whatever stopped it (background tab, page
   * change, network, phone standby), get it moving again instead of showing a still.
   */
  checkAlive() {
    if (this.closed || document.visibilityState !== "visible" || !this.el.isConnected) return;
    const now = Date.now();
    if (this.starting) {
      // Hidden while starting: the browser may not start a video at all, then the
      // attempts time out. Do not wait for that forever.
      if (now - this.startedAt > 25000) this.restart();
      return;
    }
    if (!this.mode || this.mode === "none") {
      // Nothing worked: try again, less often each time (5 s, 10 s, … up to 30 s).
      const wait = Math.min(30000, 5000 * (this.retries + 1));
      if (this.mode !== "none" && now - this.startedAt > wait) {
        this.retries++;
        this.restart();
      }
      return;
    }
    const v = this.media;
    if (v?.tagName !== "VIDEO") return; // MJPEG streams by itself
    if (v.paused) v.play?.().catch(() => {});
    if (this.mode === "hls" && v.seekable?.length) {
      const end = v.seekable.end(v.seekable.length - 1);
      if (end - v.currentTime > 6) v.currentTime = Math.max(0, end - 1); // fell behind: back to now
    }
    if (v.currentTime === this.lastTime) {
      if (++this.stuck >= 2 && now - this.startedAt > 5000) this.restart();
    } else this.stuck = 0;
    this.lastTime = v.currentTime;
  }

  /** Back from the background: HLS would play on from the old, delayed spot, so it starts
   *  over; WebRTC is real-time and only starts over if it really stopped. */
  resume() {
    if (this.starting) return; // the running attempts carry on (the watchdog checks later)
    if (!this.mode || this.mode === "hls") return this.restart();
    const v = this.media;
    if (v?.tagName !== "VIDEO") return; // MJPEG keeps streaming by itself
    v.play?.().catch(() => {});
    const t = v.currentTime;
    clearTimeout(this.aliveTimer);
    this.aliveTimer = setTimeout(() => {
      if (!this.closed && this.media === v && (v.paused || v.currentTime === t)) this.restart();
    }, 2000);
  }

  /** Start over; the last picture stays on screen until the new one plays. */
  restart() {
    this.talkStop();
    this.stopWebrtc();
    for (const v of this.pending) this.drop(v);
    this.pending.clear();
    this.mode = "";
    this.stuck = 0;
    this.lastTime = -1;
    this.start();
  }

  destroy() {
    this.closed = true;
    clearTimeout(this.aliveTimer);
    clearInterval(this.watch);
    document.removeEventListener("visibilitychange", this.onVisible);
    this.stopWebrtc();
    for (const v of this.pending) this.drop(v);
    this.pending.clear();
    if (this.media) {
      if (this.media.tagName === "IMG") this.media.src = "";
      if (this.media.tagName === "VIDEO") this.drop(this.media);
      this.media.remove();
    }
    this.still.src = "";
    this.el.remove();
  }
}

/* --------------------------------------------------------------- shared css */

const CSS = `
:host{
  --pt-plate:#24282C; --pt-plate-2:#2E3338; --pt-edge:#3C4248;
  --pt-brass:#C9A44C; --pt-brass-hi:#E2C373; --pt-brass-lo:#9B7C33; --pt-on-brass:#2B230C;
  --pt-verdigris:#4FA38A; --pt-verdigris-hi:#7FD0B7; --pt-verdigris-lo:#2F7663; --pt-on-verdigris:#0F2D24;
  --pt-ink:#F2EFE6; --pt-muted:#9AA0A6; --pt-alert:#E0704F;
  font-family:var(--ha-font-family-body,var(--paper-font-body1_-_font-family,system-ui,sans-serif));
  -webkit-font-smoothing:antialiased;
}
*{box-sizing:border-box}
button{font:inherit;color:inherit;cursor:pointer}
button:focus-visible{outline:2px solid var(--pt-brass-hi);outline-offset:3px}

.plate{background:linear-gradient(180deg,var(--pt-plate-2),var(--pt-plate) 60%);color:var(--pt-ink);
  border:1px solid var(--pt-edge);border-radius:22px;box-shadow:inset 0 1px 0 #ffffff14}
.plate-head{display:flex;align-items:center;gap:10px;padding:14px 16px 12px 18px}
.lamp{width:9px;height:9px;border-radius:50%;background:var(--pt-muted);flex:none}
.lamp.on{background:var(--pt-brass);box-shadow:0 0 0 0 color-mix(in srgb,var(--pt-brass) 60%,transparent);animation:lamp 1.6s ease-out infinite}
@keyframes lamp{to{box-shadow:0 0 0 10px color-mix(in srgb,var(--pt-brass) 0%,transparent)}}
.door-name{font-size:18px;font-weight:600;letter-spacing:.01em;margin:0;flex:1;min-width:0;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

.x{border:0;background:none;color:var(--pt-muted);width:36px;height:36px;border-radius:50%;font-size:22px;line-height:1}
.x:hover{color:var(--pt-ink);background:#ffffff10}

.window{position:relative;margin:0 12px;border-radius:14px;overflow:hidden;background:#111417;
  aspect-ratio:16/9;box-shadow:inset 0 0 0 1px #00000080,0 0 0 4px #1a1d20}
.cam,.cam-still,.cam-media{position:absolute;inset:0;width:100%;height:100%}
.cam-still,.cam-media{object-fit:cover;display:block;background:transparent}
.cam-still[src=""],.cam-still:not([src]){display:none}
.cam.live .cam-still{visibility:hidden}
.cam-media.cam-pending{opacity:0;pointer-events:none}
.cam-empty{display:grid;place-items:center;color:var(--pt-muted);font-size:14px}
.still{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.flash{position:absolute;left:12px;bottom:14px;padding:6px 12px;border-radius:999px;background:#24282ccc;
  backdrop-filter:blur(6px);color:var(--pt-brass-hi);font-size:14px;font-weight:600;opacity:0;transform:translateY(6px);
  transition:opacity .25s,transform .25s;pointer-events:none}
.flash.show{opacity:1;transform:none}
.timeline{position:absolute;left:0;right:0;bottom:0;height:3px;background:#ffffff14}
.timeline>i{display:block;height:100%;background:var(--pt-brass);transform-origin:left;transition:transform 1s linear}
.timeline.opened>i{background:var(--pt-verdigris)}

.controls{display:flex;align-items:center;gap:14px;padding:14px 18px 18px;min-height:64px}
.status{flex:1;min-width:0;font-size:13px;color:var(--pt-muted);line-height:1.5}
.status b{color:var(--pt-ink);font-weight:600}
.status .warn{color:var(--pt-alert)}
.quiet{white-space:nowrap;border:1px solid var(--pt-edge);background:none;border-radius:999px;padding:8px 14px;font-size:14px;color:var(--pt-ink)}
.quiet:hover{background:#ffffff0d}
/* Side buttons while they work: brass outline and text, never filled – the door key stays the one big button. */
.quiet.working,.pic .quiet.working,.device .quiet.working{color:var(--pt-brass-hi);border-color:var(--pt-brass);cursor:default}
.quiet.failed,.pic .quiet.failed,.device .quiet.failed{color:var(--pt-alert);border-color:var(--pt-alert);cursor:default}

/* The brass key: hold to open */
.key{position:relative;flex:none;width:76px;height:76px;border-radius:50%;border:0;padding:0;
  background:radial-gradient(circle at 35% 30%,var(--pt-brass-hi),var(--pt-brass) 55%,var(--pt-brass-lo));
  box-shadow:0 0 0 5px var(--pt-plate),0 0 0 6px var(--pt-edge),0 6px 14px #0007, inset 0 -3px 6px #0004;
  touch-action:none;user-select:none;-webkit-user-select:none;transition:transform .12s}
.key:active:not([disabled]){transform:scale(.94)}
.key svg.glyph{width:30px;height:30px;color:var(--pt-on-brass)}
.key .ring{position:absolute;inset:-9px;width:94px;height:94px;transform:rotate(-90deg);pointer-events:none}
.key .ring circle{fill:none;stroke:var(--pt-ink);stroke-width:3;stroke-linecap:round;stroke-dasharray:70 213;opacity:0}
.key.busy .ring{animation:spin .9s linear infinite}
.key.busy .ring circle{opacity:1}
@keyframes spin{to{transform:rotate(270deg)}}
.key.done{background:radial-gradient(circle at 35% 30%,var(--pt-verdigris-hi),var(--pt-verdigris) 60%,var(--pt-verdigris-lo))}
.key.done svg.glyph{color:var(--pt-on-verdigris)}
.key[disabled]:not(.done):not(.busy){filter:grayscale(1) brightness(.6);cursor:not-allowed}
.key.busy{cursor:progress}
.key.done{cursor:default}
.key-wrap{display:flex;flex-direction:column;align-items:center;gap:8px}
.key-label{font-size:12px;color:var(--pt-muted);white-space:nowrap}
.key-label.ok{color:var(--pt-verdigris)}
.key-label.bad{color:var(--pt-alert)}

/* Two-way audio: speaker toggle + push-to-talk */
.talk-wrap{display:flex;align-items:flex-end;gap:10px;flex:none}
.talk-wrap[hidden]{display:none}
.mic-wrap{display:flex;flex-direction:column;align-items:center;gap:4px}
.spk,.mic{border:0;padding:0;display:grid;place-items:center;color:#fff;border-radius:50%;
  background:#0000004d;backdrop-filter:blur(6px);box-shadow:0 0 0 1px #ffffff40;
  touch-action:none;user-select:none;-webkit-user-select:none;transition:background .15s,transform .12s}
.spk{width:38px;height:38px;margin-bottom:20px}
.spk svg{width:20px;height:20px}
.spk.on{background:#ffffff40}
.mic{width:56px;height:56px}
.mic svg{width:26px;height:26px}
.mic:hover:not([disabled]){background:#00000080}
.mic.on{background:var(--pt-verdigris);box-shadow:0 0 0 4px color-mix(in srgb,var(--pt-verdigris) 33%,transparent);transform:scale(1.06)}
.mic[disabled]{opacity:.45;cursor:not-allowed}
.talk-label{font-size:11px;color:#ffffffd9;white-space:nowrap;text-shadow:0 1px 3px #000c}

@media (prefers-reduced-motion:reduce){
  .lamp.on,.key.busy .ring{animation:none}.timeline>i,.flash,.key,.mic,.spk{transition:none}
}
`;

const KEY_SVG = `<svg class="glyph" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M7 14a2 2 0 1 1 0-4 2 2 0 0 1 0 4Zm5.65-4A6 6 0 1 0 7 18a6 6 0 0 0 5.65-4H17v4h4v-4h2v-4H12.65Z"/></svg>`;
const CHECK_SVG = `<svg class="glyph" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4L9 16.2Z"/></svg>`;

/** Wires a .key button: one tap opens, with clear feedback while it works. */
function wireKey(btn, label, onOpen) {
  const idle = () => {
    btn.disabled = false;
    btn.classList.remove("done", "busy");
    btn.innerHTML = KEY_SVG + RING_SVG;
    label.textContent = "Tür öffnen";
    label.classList.remove("ok", "bad");
  };
  btn.addEventListener("click", async () => {
    if (btn.disabled) return;
    btn.disabled = true;
    btn.classList.add("busy");
    label.textContent = "Öffnet …";
    navigator.vibrate?.(15);
    try {
      await onOpen();
      btn.classList.remove("busy");
      btn.classList.add("done");
      btn.innerHTML = CHECK_SVG;
      label.textContent = "Geöffnet";
      label.classList.add("ok");
      navigator.vibrate?.([30, 40, 30]);
      setTimeout(idle, 3000);
    } catch (err) {
      btn.classList.remove("busy");
      label.textContent = err?.message || "Fehlgeschlagen";
      label.classList.add("bad");
      setTimeout(idle, 4000);
    }
  });
}
const RING_SVG = `<svg class="ring" viewBox="0 0 100 100" aria-hidden="true"><circle cx="50" cy="50" r="45"/></svg>`;

function keyMarkup(canOpen) {
  if (!canOpen) return "";
  return `<div class="key-wrap">
    <button class="key" aria-label="Tür öffnen">${KEY_SVG}${RING_SVG}</button>
    <span class="key-label">Tür öffnen</span>
  </div>`;
}

/** Does the WebRTC answer accept audio from us (camera speaker / back channel)? */
function answerAcceptsAudio(sdp) {
  const audio = String(sdp || "")
    .split(/\r?\nm=/)
    .find((s) => s.startsWith("audio") || s.startsWith("m=audio"));
  if (!audio || /^m?=?audio 0 /.test(audio)) return false;
  if (/a=(sendonly|inactive)/.test(audio)) return false;
  return true; // sendrecv, recvonly or no direction (= sendrecv)
}

const MIC_SVG = `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 14a3 3 0 0 0 3-3V5a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2Z"/></svg>`;
const SPK_SVG = `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M3 9v6h4l5 5V4L7 9H3Zm13.5 3A4.5 4.5 0 0 0 14 8v8a4.5 4.5 0 0 0 2.5-4Z"/></svg>`;
const MUTE_SVG = `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M3 9v6h4l5 5V4L7 9H3Zm18.6 1.4-1.4-1.4L18 11.2 15.8 9l-1.4 1.4 2.2 2.2-2.2 2.2 1.4 1.4 2.2-2.2 2.2 2.2 1.4-1.4-2.2-2.2 2.2-2.2Z"/></svg>`;

function talkMarkup() {
  return `<div class="talk-wrap" hidden>
    <button class="spk" aria-label="Ton der Kamera" aria-pressed="false">${MUTE_SVG}</button>
    <div class="mic-wrap">
      <button class="mic" aria-label="Gedrückt halten zum Sprechen" disabled>${MIC_SVG}</button>
      <span class="talk-label">Verbindet …</span>
    </div>
  </div>`;
}

const TALK_TEXT = {
  wait: "Verbindet …",
  ready: "Halten zum Sprechen",
  unsupported: "Kamera hört nicht",
  insecure: "Nur mit HTTPS",
};

/**
 * Wires speaker + push-to-talk buttons for a CameraView. Returns onTalk(state) for the view.
 * getView() returns the current CameraView (or null).
 */
function wireTalk(root, getView) {
  const wrap = root.querySelector(".talk-wrap");
  const mic = root.querySelector(".mic");
  const spk = root.querySelector(".spk");
  const label = root.querySelector(".talk-label");
  let state = "wait";
  let active = false;
  const setSpk = (on) => {
    spk.classList.toggle("on", on);
    spk.setAttribute("aria-pressed", String(on));
    spk.innerHTML = on ? SPK_SVG : MUTE_SVG;
  };
  spk.addEventListener("click", () => setSpk(!!getView()?.toggleSound()));
  const start = async (e) => {
    if (mic.disabled || active || (e.button !== undefined && e.button !== 0)) return;
    e.preventDefault();
    active = true;
    try {
      mic.setPointerCapture?.(e.pointerId);
    } catch {
      /* ignore */
    }
    mic.classList.add("on");
    label.textContent = "Spricht …";
    navigator.vibrate?.(10);
    try {
      await getView()?.talkStart();
      if (!active) getView()?.talkStop(); // released while the microphone was starting
      else setSpk(true);
    } catch (err) {
      active = false;
      mic.classList.remove("on");
      label.textContent = err?.message || "Fehlgeschlagen";
      setTimeout(() => !active && (label.textContent = TALK_TEXT[state]), 3000);
    }
  };
  const stop = () => {
    if (!active) return;
    active = false;
    getView()?.talkStop();
    mic.classList.remove("on");
    label.textContent = TALK_TEXT[state];
  };
  mic.addEventListener("pointerdown", start);
  for (const ev of ["pointerup", "pointercancel", "lostpointercapture"]) mic.addEventListener(ev, stop);
  mic.addEventListener("contextmenu", (e) => e.preventDefault());
  // Keyboard: hold Space/Enter
  mic.addEventListener("keydown", (e) => (e.key === " " || e.key === "Enter") && !e.repeat && start(e));
  mic.addEventListener("keyup", (e) => (e.key === " " || e.key === "Enter") && stop());
  return {
    onTalk(s) {
      state = s;
      wrap.hidden = false;
      mic.disabled = s !== "ready";
      if (!active) label.textContent = TALK_TEXT[s] || "";
    },
    reset() {
      stop();
      setSpk(false);
      wrap.hidden = true;
    },
  };
}

/* --------------------------------------------------------------- controller */

class PortierController {
  constructor() {
    this.doors = new Map(); // entry_id -> snapshot
    this.offset = 0; // server clock - local clock (ms)
    this.listeners = new Set();
    this.chime = new Chime();
    this.overlay = null;
    this.conn = null;
    this.seen = new Map(); // entry_id -> last_ring this page has already handled
    // Since when this page has been on screen without interruption (local clock, ms).
    // Live picture and sound only for rings that happen while the page is on screen;
    // opening the app after a ring shows nothing late – the notification covers that.
    this.visibleSince = document.visibilityState === "visible" ? Date.now() : Infinity;
    this.connect();
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState !== "visible") {
        this.visibleSince = Infinity;
        return;
      }
      this.visibleSince = Date.now();
      // Back on screen: renew a possibly stale subscription right away.
      if (this.conn?.connected) this.subscribe();
    });
  }

  now() {
    return Date.now() + this.offset;
  }

  async connect() {
    const conn = await this.waitForConnection();
    this.conn = conn;
    // After an HA restart the page reconnects before Portier is loaded again, so the
    // library's automatic re-subscribe fails once and is never retried. We handle
    // re-subscribing ourselves and keep retrying until Portier answers.
    conn.addEventListener("ready", () => this.subscribe());
    // Home Assistant announces every integration as soon as it has finished loading.
    // The moment Portier is up again after a restart, subscribe – no waiting.
    conn
      .subscribeEvents((ev) => {
        if (ev?.data?.component !== "portier" || this.unsub) return;
        this.wake ? this.wake() : this.subscribe();
      }, "component_loaded")
      .catch(() => {});
    conn.addEventListener("disconnected", () => {
      this.unsub = null;
    });
    this.subscribe();
    // Safety net: if nothing arrived for a while although we think we're connected, retry.
    setInterval(() => {
      if (this.conn?.connected && !this.unsub && !this.subscribing) this.subscribe();
    }, 5000);
  }

  async subscribe() {
    if (this.subscribing) return;
    this.subscribing = true;
    try {
      this.unsub?.();
    } catch {
      /* already gone */
    }
    this.unsub = null;
    let delay = 1000;
    try {
      while (this.conn?.connected !== false) {
        try {
          this.unsub = await this.conn.subscribeMessage((msg) => this.onMessage(msg), { type: "portier/subscribe" }, { resubscribe: false });
          return;
        } catch (err) {
          if (err?.code === "unknown_command" || err?.code === 3 || err === 3) {
            // Portier not loaded (yet) – typical right after an HA restart.
          } else {
            console.info("Portier: Anmeldung fehlgeschlagen, neuer Versuch", err?.message || err);
          }
          // Retry in 2 s, or right away when HA reports that Portier has loaded.
          await new Promise((r) => {
            const t = setTimeout(r, delay);
            this.wake = () => {
              clearTimeout(t);
              r();
            };
          });
          this.wake = null;
          delay = 2000;
        }
      }
    } finally {
      this.subscribing = false;
    }
  }

  async waitForConnection() {
    for (;;) {
      if (window.hassConnection) return (await window.hassConnection).conn;
      const h = rootHass();
      if (h?.connection) return h.connection;
      await new Promise((r) => setTimeout(r, 500));
    }
  }

  /** The server runs a newer Portier than this page: reload once to pick it up. */
  checkUpdate(serverUrl) {
    const mine = new URL(import.meta.url).searchParams.get("v");
    const theirs = serverUrl ? new URL(serverUrl, location.origin).searchParams.get("v") : null;
    if (!mine || !theirs || mine === theirs) return;
    const key = `portier.reloaded.${theirs}`;
    try {
      if (sessionStorage.getItem(key)) return;
      sessionStorage.setItem(key, "1");
    } catch {
      return;
    }
    console.info("Portier: neue Version, Seite wird neu geladen");
    // Fetch the new file past the browser cache first, so the reload really gets it.
    fetch(serverUrl, { cache: "reload" })
      .catch(() => {})
      .finally(() => location.reload());
  }

  callWS(msg) {
    const h = rootHass();
    return h ? h.callWS(msg) : this.conn.sendMessagePromise(msg);
  }

  /** A still taken by Portier (same way as the ring picture) – for cameras whose
   *  Home Assistant still fails. Resolves to a data: URL, or "" if there is none. */
  async portierStill(entryId) {
    try {
      const { content_type: type, data } = await this.callWS({ type: "portier/still", entry_id: entryId });
      return `data:${type};base64,${data}`;
    } catch {
      return "";
    }
  }

  onMessage(msg) {
    if (msg.server_time) this.offset = new Date(msg.server_time).getTime() - Date.now();
    if (msg.type === "snapshot") {
      this.checkUpdate(msg.frontend);
      this.doors = new Map(msg.doors.map((d) => [d.entry_id, d]));
      this.look = msg.look || LOOK_DEFAULT;
      applyLook(this.overlay, this.look);
      this.catchUp();
      this.overlay?.sync();
    } else if (msg.type === "ring") {
      const door = this.doors.get(msg.entry_id);
      if (door) Object.assign(door, { ring_id: msg.ring_id, last_ring: msg.time, view_until: msg.view_until });
      this.seen.set(msg.entry_id, msg.time);
      this.ring(msg.entry_id, msg);
    } else if (msg.type === "dismiss") {
      // The ring's live view was closed on another device.
      this.overlay?.dismissed(msg.entry_id, msg.ring_time);
    } else if (msg.type === "show") {
      this.show(msg.entry_id, { manual: true });
    }
    for (const fn of this.listeners) fn(msg);
  }

  /** Was the page on screen when this ring happened? */
  onScreenAt(iso) {
    const ringLocal = new Date(iso).getTime() - this.offset;
    // a little slack for clock differences
    return document.visibilityState === "visible" && ringLocal >= this.visibleSince - 1000;
  }

  /**
   * A ring that happened while the page was on screen but its subscription was briefly
   * gone (reconnecting) still shows up with the next snapshot – only a few seconds late.
   */
  catchUp() {
    for (const door of this.doors.values()) {
      const ring = door.last_ring;
      if (!ring || this.seen.get(door.entry_id) === ring) continue;
      this.seen.set(door.entry_id, ring);
      const opened = door.last_opened && door.last_opened >= ring;
      if (opened || this.overlay?.views.has(door.entry_id)) continue;
      this.ring(door.entry_id, { time: ring, late: true });
    }
  }

  async ring(entryId, msg) {
    const door = this.doors.get(entryId);
    if (!door) return;
    // A ring that reaches us more than a few seconds late (app was asleep) gets nothing.
    if (this.now() - new Date(msg.time).getTime() > LATE_MS) return;
    const settings = deviceSettings();
    if (!this.onScreenAt(msg.time)) {
      // Home Assistant is open in a background tab: the ring is happening now, so the
      // sound plays (once per browser), but no picture – and nothing later on.
      // Not in the Home Assistant app: there the push notification already rings.
      if (document.visibilityState !== "hidden" || isHaApp()) return;
      if (!settings.sound || door.sound === "off" || !this.chime.ready) return;
      // Let a tab that is on screen claim the sound first.
      await new Promise((r) => setTimeout(r, 300));
      try {
        if (await this.claimSound(entryId, String(msg.time))) this.chime.play(door.sound, door.volume);
      } catch (err) {
        console.warn("Portier: Klingelton fehlgeschlagen", err);
      }
      return;
    }
    // Picture first: nothing that happens with the sound may keep it from showing.
    if (settings.overlay) this.show(entryId, { ring: true });
    if (!settings.sound || door.sound === "off") return;
    let audible = true;
    try {
      if (await this.claimSound(entryId, String(msg.time))) audible = this.chime.play(door.sound, door.volume);
    } catch (err) {
      console.warn("Portier: Klingelton fehlgeschlagen", err);
      audible = false;
    }
    if (!audible && settings.overlay) this.overlay?.soundBlocked(entryId);
  }

  /** Only one tab of this browser plays the sound for a given ring. */
  async claimSound(entryId, ringTime) {
    const key = LS_PLAYED + entryId;
    const claim = () => {
      try {
        if (localStorage.getItem(key) === ringTime) return false;
        localStorage.setItem(key, ringTime);
      } catch {
        /* storage blocked: just play */
      }
      return true;
    };
    if (navigator.locks?.request) return navigator.locks.request(`portier-sound-${entryId}`, claim);
    return claim();
  }

  overlayRoot() {
    if (!this.overlay) {
      this.overlay = document.createElement("portier-overlay");
      this.overlay.controller = this;
      applyLook(this.overlay, this.look);
      document.body.append(this.overlay);
    }
    return this.overlay;
  }

  show(entryId, opts = {}) {
    this.overlayRoot().open(entryId, opts);
  }

  async open(entryId) {
    try {
      // (The server also recognises the app by its login, which is the reliable part.)
      const client = isHaApp() ? "ha_app" : "browser";
      await this.callWS({ type: "portier/open", entry_id: entryId, client });
    } catch (err) {
      throw new Error(err?.message || "Öffnen fehlgeschlagen");
    }
  }

  dismiss(entryId, ringTime) {
    return this.callWS({ type: "portier/dismiss", entry_id: entryId, ring_time: ringTime }).catch(() => {});
  }

  async testRing(entryId) {
    const before = this.doors.get(entryId)?.last_ring;
    await this.callWS({ type: "portier/ring", entry_id: entryId });
    // The ring itself comes back over our subscription. If it does not arrive,
    // the subscription is stale: renew it, the new snapshot then shows the ring.
    setTimeout(() => {
      if (this.doors.get(entryId)?.last_ring === before) this.subscribe();
    }, 1500);
  }
}

/* ------------------------------------------------------------------ overlay */

/* The live picture is the whole plate: name, status and key float on top of it.
   Shared by the ring overlay and the door cards in the panel. */
const PICTURE_CSS = `
.plate.pic{position:relative;overflow:hidden;border:0;border-radius:18px;background:#111417}
.pic .window{margin:0;border-radius:0;box-shadow:none}
.pic .plate-head,.pic .controls{position:absolute;left:0;right:0;z-index:1;color:#fff;text-shadow:0 1px 3px #000c}
.pic .plate-head{top:0;padding:10px 8px 28px 16px;background:linear-gradient(#000000b3,#0000)}
.pic .controls{bottom:0;min-height:0;align-items:flex-end;padding:32px 16px 14px;
  background:linear-gradient(#0000,#000000c0)}
.pic .door-name{font-size:17px}
.pic .status,.pic .key-label:not(.ok):not(.bad){color:#ffffffd9}
.pic .status b{color:#fff}
.pic .x,.pic .gear{color:#fff;background:#0000004d;backdrop-filter:blur(6px);width:34px;height:34px}
.pic .x:hover,.pic .gear:hover{background:#00000080}
.pic .lamp:not(.on){background:#ffffff80}
.pic .flash{left:50%;top:52px;bottom:auto;transform:translate(-50%,-6px);white-space:nowrap}
.pic .flash.show{transform:translate(-50%,0)}
.pic .timeline{z-index:2}
.pic .key{width:64px;height:64px;box-shadow:0 0 0 3px #0000004d,0 6px 14px #0007,inset 0 -3px 6px #0004}
.pic .key .ring{inset:-8px;width:80px;height:80px}
.pic .key svg.glyph{width:26px;height:26px}
.pic .key-wrap{gap:4px}
.pic .key-label{font-size:11px}
`;

class PortierOverlay extends HTMLElement {
  constructor() {
    super();
    this.views = new Map(); // entry_id -> {el, camera, tick}
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `<style>${CSS}
      :host{position:fixed;z-index:2147483000;right:max(16px,env(safe-area-inset-right));
        bottom:max(16px,env(safe-area-inset-bottom));width:var(--w,560px);pointer-events:none;
        transition:width .32s cubic-bezier(.2,.8,.2,1)}
      /* padding keeps the plate shadows inside the scroll area */
      #stack{display:grid;gap:12px;grid-template-columns:repeat(var(--cols,1),minmax(0,1fr));align-items:stretch;
        max-height:calc(100dvh - 32px + 48px);overflow:auto;overscroll-behavior:contain;scrollbar-width:none;
        padding:24px;margin:-24px}
      ${PICTURE_CSS}
      .plate{pointer-events:auto;box-shadow:0 12px 28px #0009;animation:rise .32s cubic-bezier(.2,.8,.2,1)}
      .plate.leaving{animation:sink .22s ease-in forwards}
      .plate.fresh{box-shadow:0 0 0 2px var(--pt-brass),0 12px 28px #0009}
      @keyframes rise{from{opacity:0;transform:translateY(24px) scale(.96)}}
      @keyframes sink{to{opacity:0;transform:scale(.94)}}
      /* smaller plates when several doors are shown */
      :host([data-compact]) .plate{border-radius:14px}
      :host([data-compact]) .plate-head{padding:8px 6px 22px 12px}
      :host([data-compact]) .door-name{font-size:15px}
      :host([data-compact]) .controls{padding:24px 12px 10px;gap:10px}
      :host([data-compact]) .status{font-size:12px}
      :host([data-compact]) .key{width:50px;height:50px}
      :host([data-compact]) .key .ring{inset:-7px;width:64px;height:64px}
      :host([data-compact]) .key svg.glyph{width:22px;height:22px}
      :host([data-compact]) .key-label{display:none}
      :host([data-compact]) .talk-label{display:none}
      :host([data-compact]) .mic{width:44px;height:44px}
      :host([data-compact]) .spk{width:32px;height:32px;margin-bottom:6px}
      :host([data-compact]) .flash{top:44px;font-size:13px}
      @media (max-width:600px){:host{left:8px;right:8px;bottom:max(8px,env(safe-area-inset-bottom));width:auto}}
      @media (prefers-reduced-motion:reduce){:host{transition:none}.plate,.plate.leaving{animation:none}}
    </style><div id="stack" role="region" aria-live="polite" aria-label="Türklingel"></div>`;
    this.stack = root.getElementById("stack");
    this.onResize = () => this.layout(false);
    window.addEventListener("resize", this.onResize);
  }

  /**
   * Arrange all open doors: side by side in landscape, stacked in portrait.
   * One door is big; each extra door makes them smaller so all stay visible.
   */
  layout(animate = true) {
    const plates = [...this.stack.querySelectorAll(".plate:not(.leaving)")];
    const n = plates.length;
    if (!n) return;
    const before = animate ? new Map(plates.map((p) => [p, p.getBoundingClientRect()])) : null;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const gap = 12;
    const chrome = 0; // name and controls sit inside the picture
    const landscape = vw >= 760 && vw >= vh * 1.05;
    let cols = 1;
    let rows = n;
    if (landscape && n > 1) {
      cols = n <= 3 ? n : Math.ceil(n / 2);
      rows = Math.ceil(n / cols);
    }
    // Several doors share at most ~80 % of the screen width, so the dashboard stays visible.
    const avail = cols === 1 ? vw - 32 : Math.min(vw - 32, vw * 0.8);
    const preferred = [560, 460, 380, 340][Math.min(cols, 4) - 1];
    // Largest column width that still fits all rows into the screen height.
    const byHeight = ((vh - 32 - (rows - 1) * gap) / rows - chrome) / 0.5625;
    const colW = Math.max(240, Math.min(preferred, byHeight, (avail - (cols - 1) * gap) / cols));
    this.style.setProperty("--cols", cols);
    this.style.setProperty("--w", `${Math.round(colW * cols + (cols - 1) * gap)}px`);
    this.toggleAttribute("data-compact", n > 1);
    if (!before || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    for (const p of plates) {
      const a = before.get(p);
      const b = p.getBoundingClientRect();
      if (!a || !a.width || !b.width) continue;
      const dx = a.left - b.left;
      const dy = a.top - b.top;
      const sx = a.width / b.width;
      const sy = a.height / b.height;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1 && Math.abs(sx - 1) < 0.01) continue;
      p.animate(
        [
          { transformOrigin: "top left", transform: `translate(${dx}px,${dy}px) scale(${sx},${sy})` },
          { transformOrigin: "top left", transform: "none" },
        ],
        { duration: 340, easing: "cubic-bezier(.2,.8,.2,1)" },
      );
    }
  }

  open(entryId, { ring = false, manual = false, blocked = false } = {}) {
    const door = this.controller.doors.get(entryId);
    if (!door) return;
    let v = this.views.get(entryId);
    if (!v) {
      v = this.build(door);
      this.views.set(entryId, v);
      this.layout();
    }
    if (manual && !ring) {
      v.until = this.controller.now() + door.live_seconds * 1000;
    } else {
      v.until = door.view_until ? new Date(door.view_until).getTime() : this.controller.now() + door.live_seconds * 1000;
    }
    v.total = Math.max(1000, v.until - this.controller.now());
    v.ringing = ring;
    // Remember which ring this view belongs to; closing it then closes it everywhere.
    if (ring) {
      v.opened = false;
      v.ringTime = door.last_ring;
    }
    if (ring) {
      this.flash(v, blocked ? "Ton blockiert – einmal tippen" : "Es klingelt");
      v.el.classList.add("fresh");
      clearTimeout(v.freshTimer);
      v.freshTimer = setTimeout(() => v.el.classList.remove("fresh"), 6000);
    }
    this.render(v);
  }

  build(door) {
    const el = document.createElement("section");
    el.className = "plate pic";
    el.setAttribute("aria-label", door.name);
    el.innerHTML = `
      <div class="window">
        <div class="plate-head">
          <span class="lamp on"></span>
          <h2 class="door-name">${esc(door.name)}</h2>
          <button class="x" aria-label="Schließen">×</button>
        </div>
        <div class="flash"></div>
        <div class="controls">
          <div class="status"></div>
          ${talkMarkup()}
          ${keyMarkup(door.can_open)}
        </div>
        <div class="timeline"><i></i></div>
      </div>`;
    let camera = null;
    const talk = wireTalk(el, () => camera);
    camera = new CameraView(door.camera, rootHass, { talk: !!door.talk, onTalk: talk.onTalk, entryId: door.entry_id });
    el.querySelector(".window").prepend(camera.el);
    camera.start();
    const v = { entryId: door.entry_id, el, camera, until: 0, total: 1, ringing: false };
    el.querySelector(".x").addEventListener("click", () => {
      if (v.ringTime) this.controller.dismiss(door.entry_id, v.ringTime);
      this.close(door.entry_id);
    });
    if (door.can_open) wireKey(el.querySelector(".key"), el.querySelector(".key-label"), () => this.controller.open(door.entry_id));
    v.tick = setInterval(() => this.render(v), 1000);
    this.stack.prepend(el);
    return v;
  }

  soundBlocked(entryId) {
    const v = this.views.get(entryId);
    if (v) this.flash(v, "Ton blockiert – einmal tippen");
  }

  flash(v, text) {
    const f = v.el.querySelector(".flash");
    f.textContent = text;
    f.classList.add("show");
    clearTimeout(v.flashTimer);
    v.flashTimer = setTimeout(() => f.classList.remove("show"), 4000);
  }

  sync() {
    for (const v of this.views.values()) {
      const door = this.controller.doors.get(v.entryId);
      if (!door) {
        this.close(v.entryId);
        continue;
      }
      // Door was opened (here or elsewhere) after the last ring: switch to the short window.
      const opened = door.last_opened && (!door.last_ring || door.last_opened >= door.last_ring);
      if (opened && door.view_until) {
        const until = new Date(door.view_until).getTime();
        if (Math.abs(until - v.until) > 1500) {
          // Keep the line where it is and let it run out exactly when the picture closes,
          // instead of jumping back to full.
          const now = this.controller.now();
          const shown = Math.max(0, Math.min(1, (v.until - now) / v.total));
          const left = Math.max(1000, until - now);
          v.until = until;
          v.total = shown > 0.02 ? left / shown : left;
        }
      }
      v.opened = !!opened;
      this.render(v);
    }
  }

  render(v) {
    const door = this.controller.doors.get(v.entryId);
    if (!door) return;
    const left = v.until - this.controller.now();
    if (left <= 0) return this.close(v.entryId);
    const el = v.el;
    el.querySelector(".lamp").classList.toggle("on", !!v.ringing && !v.opened);
    const line = el.querySelector(".timeline");
    line.classList.toggle("opened", !!v.opened);
    line.firstElementChild.style.transform = `scaleX(${Math.max(0, Math.min(1, left / v.total))})`;
    // How long the picture stays is shown by the line at the bottom only.
    const status = v.opened ? `<b>Tür geöffnet</b>${openedBy(door)}` : "";
    const box = el.querySelector(".status");
    if (box.innerHTML !== status) box.innerHTML = status;
  }

  /** Close a ring view because it was closed on another device. Manual views stay. */
  dismissed(entryId, ringTime) {
    const v = this.views.get(entryId);
    if (v && v.ringTime && v.ringTime === ringTime) this.close(entryId);
  }

  close(entryId) {
    const v = this.views.get(entryId);
    if (!v) return;
    this.views.delete(entryId);
    clearInterval(v.tick);
    v.el.classList.add("leaving");
    setTimeout(() => {
      v.camera.destroy();
      v.el.remove();
      this.layout();
    }, 220);
  }
}

/* ------------------------------------------------------------------- editor */

const EDITOR_CSS = `
dialog.editor{border:0;padding:0;background:transparent;color:var(--pt-ink);width:min(640px,100vw - 24px);max-height:calc(100dvh - 24px)}
dialog.editor::backdrop{background:#0009;backdrop-filter:blur(2px)}
.editor form{display:flex;flex-direction:column;max-height:calc(100dvh - 24px)}
.editor .plate{display:flex;flex-direction:column;min-height:0;overflow:hidden}
.editor .scroll{overflow:auto;padding:4px 20px 8px;display:grid;gap:4px}
.editor h2{font-size:19px;margin:0;flex:1}
.editor fieldset{border:0;border-top:1px solid var(--pt-edge);margin:0;padding:14px 0 10px;display:grid;gap:14px}
.editor fieldset:first-of-type{border-top:0}
.editor legend,.editor summary{font-size:13px;color:var(--pt-brass-hi);font-weight:600;padding:0;margin-bottom:4px}
.editor details{border-top:1px solid var(--pt-edge);padding:12px 0;margin:0}
.editor details[open] summary{margin-bottom:14px}
.editor details>div{display:grid;gap:14px}
.editor .sub{margin:4px 0 -6px;font-size:12px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--pt-muted)}
.editor .sub-block{display:grid;gap:14px;padding:4px 0 0 14px;border-left:2px solid var(--pt-edge)}
.editor .sub-block[hidden]{display:none}
.editor summary{cursor:pointer;list-style:revert}
/* Settings that belong to the field above (camera, NFC): indented, joined by a line. */
.editor .cam-opts{margin-top:-4px}

.f{display:grid;gap:6px}
.f[hidden]{display:none}
.f>span{font-size:14px}
.f small{font-size:12px;color:var(--pt-muted);line-height:1.4;max-width:62ch}
.f small.err{color:var(--pt-alert)}
.f input[type=text],.f input[type=number],.f input[type=time],.f select{color-scheme:dark;width:100%;font:inherit;font-size:15px;color:var(--pt-ink);
  background:#1a1d20;border:1px solid var(--pt-edge);border-radius:10px;padding:10px 12px;min-height:44px}
/* Same arrow as the device pickers (▾ in a 38px zone at the right), not the browser's own. */
.f select{appearance:none;-webkit-appearance:none;padding-right:40px;cursor:pointer;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='9' height='6'%3E%3Cpath d='M0 0h9L4.5 6z' fill='%239AA0A6'/%3E%3C/svg%3E");
  background-repeat:no-repeat;background-position:right 16px center}
.f input:focus-visible,.f select:focus-visible{outline:2px solid var(--pt-brass);outline-offset:1px;border-color:transparent}
.f.bad input,.f.bad select{border-color:var(--pt-alert)}
.ent{display:flex;gap:6px}
.ent .combo{flex:1;min-width:0;position:relative}
.ent .combo input{padding-right:40px}
.ent .drop{position:absolute;right:2px;top:2px;bottom:2px;width:38px;border:0;background:none;color:var(--pt-muted);font-size:16px;cursor:pointer;border-radius:8px}
.ent .drop:hover{color:var(--pt-ink)}
.ent .dd{position:absolute;z-index:5;left:0;right:0;top:calc(100% + 4px);margin:0;padding:4px;list-style:none;text-align:left;max-height:260px;overflow:auto;
  background:#1a1d20;border:1px solid var(--pt-edge);border-radius:10px;box-shadow:0 10px 30px #000a}
.ent .dd[hidden],.ent .dd li[hidden]{display:none}
.ent .dd li{display:grid;padding:7px 10px;border-radius:7px;cursor:pointer;gap:1px}
.ent .dd li b{font-weight:500;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ent .dd li small{font-size:12px;color:var(--pt-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ent .dd li.hi{background:#ffffff12}
.ent .dd li.cur b{color:var(--pt-brass-hi)}
.ent .dd li.empty{color:var(--pt-muted);font-style:italic;font-size:14px}
.ent .dd li.none{color:var(--pt-muted);cursor:default;font-size:13px}
.ent-name{font-size:12px;color:var(--pt-verdigris)}
.ent-name.missing{color:var(--pt-alert)}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.two[hidden]{display:none}
@media (max-width:520px){.two{grid-template-columns:1fr}}
.chk{display:flex;align-items:center;gap:10px;font-size:14px;cursor:pointer}
.chk input{accent-color:var(--pt-brass);width:18px;height:18px;margin:0;flex:none}
.checks{display:grid;gap:8px;padding:10px 12px;background:#1a1d20;border:1px solid var(--pt-edge);border-radius:10px}
.inline{display:flex;gap:10px;align-items:center}
.inline select{flex:1}
.inline input[type=range]{flex:1;accent-color:var(--pt-brass)}
.inline output{width:3.5em;text-align:right;font-variant-numeric:tabular-nums;color:var(--pt-muted)}
.foot{display:flex;gap:10px;align-items:center;padding:14px 20px 18px;border-top:1px solid var(--pt-edge)}
.foot .grow{flex:1}
.primary{border:0;border-radius:999px;padding:10px 20px;background:var(--pt-brass);color:var(--pt-on-brass);font-weight:600;font-size:15px}
.primary:hover{background:var(--pt-brass-hi)}
.primary[disabled]{opacity:.6}
.danger{border:1px solid #e0704f66;background:none;color:var(--pt-alert);border-radius:999px;padding:9px 14px;font-size:14px}
.danger.armed{background:var(--pt-alert);color:#fff;border-color:transparent}
.formerr{color:var(--pt-alert);font-size:13px;padding:0 20px}
`;

const DOMAINS = {
  trigger: ["binary_sensor", "event", "input_boolean", "input_button", "button", "switch"],
  camera: ["camera"],
  opener: ["switch", "light", "input_boolean", "button", "input_button", "lock", "script", "cover"],
  contact: ["binary_sensor", "input_boolean", "lock", "cover"],
  gong: ["switch", "light", "input_boolean", "button", "input_button", "script", "siren"],
};

const ERRORS = {
  same_entity: "Der Türöffner darf nicht gleichzeitig Klingeltaster oder Gong sein.",
  opener_in_use: "Dieser Türöffner gehört schon zu einer anderen Tür.",
  nfc_needs_phone: "Bitte mindestens ein Handy auswählen, das mit dem Aufkleber öffnen darf.",
  nfc_needs_opener: "Öffnen per NFC braucht einen Türöffner.",
};

function notifyLabel(service, labels) {
  if (labels?.[service]) return `📱 ${labels[service]}`;
  if (service.startsWith("mobile_app_")) return `📱 ${service.slice(11).replace(/_/g, " ")}`;
  return service.replace(/_/g, " ");
}

class PortierEditor extends HTMLElement {
  /** "Who may open": admins only, and opening by NFC tag behind a switch. */
  accessMarkup(v, cfg) {
    const ticks = (name, items, chosen) =>
      items
        .map(
          (it) => `<label class="chk"><input type="checkbox" name="${name}" value="${esc(it.value)}"
            ${(chosen || []).includes(it.value) ? "checked" : ""}>${esc(it.label)}</label>`,
        )
        .join("");
    const tags = (cfg.nfc_tags || []).map((t) => ({ value: t.entity_id, label: t.name }));
    for (const id of v.nfc_tags || []) if (!tags.some((t) => t.value === id)) tags.push({ value: id, label: id });
    const phones = (cfg.nfc_phones || []).map((p) => ({ value: p.device_id, label: p.name }));
    for (const id of v.nfc_devices || []) if (!phones.some((p) => p.value === id)) phones.push({ value: id, label: "Unbekanntes Handy" });
    const hhmm = (t) => (t ? String(t).slice(0, 5) : "");
    const on = (v.nfc_tags || []).length > 0;
    return `<fieldset><legend>Wer darf öffnen</legend>
          ${this.check("open_admin_only", "Nur Administratoren dürfen öffnen", v.open_admin_only, "Sonst jeder angemeldete Benutzer, der die Kamera sehen darf, z. B. das Familien-Tablet.")}
          ${this.check("nfc_on", "Öffnen per NFC-Aufkleber", on, "Handy mit der Home-Assistant-App an einen Aufkleber an der Tür halten – ohne Tippen. Praktisch für Kinder.")}
          <div class="nfc-body sub-block"${on ? "" : " hidden"}>
          <div class="f" data-f="nfc_tags"><span>NFC-Aufkleber an der Tür</span>
            <div class="checks">${ticks("nfc_tags", tags, v.nfc_tags) || `<small>Noch kein Aufkleber bekannt. Das Handy mit der Home-Assistant-App einmal an den Aufkleber halten (oder unter Einstellungen → Tags einen anlegen), dann erscheint er hier.</small>`}</div>
            <small>Das Handy muss Home Assistant erreichen, z. B. über das WLAN an der Tür.</small></div>
          <div class="f" data-f="nfc_devices"><span>Handys, die öffnen dürfen</span>
            <div class="checks">${ticks("nfc_devices", phones, v.nfc_devices) || `<small>Keine Handys mit der Home-Assistant-App gefunden.</small>`}</div>
            <small>Nur diese Handys öffnen. Andere Handys am Aufkleber bewirken nichts.</small></div>
          <div class="two">
            <label class="f" data-f="nfc_from"><span>Erlaubt ab</span><input type="time" name="nfc_from" value="${hhmm(v.nfc_from)}"></label>
            <label class="f" data-f="nfc_until"><span>Erlaubt bis</span><input type="time" name="nfc_until" value="${hhmm(v.nfc_until)}"></label>
          </div>
          <small>Beide Zeiten leer = rund um die Uhr.</small>
          ${this.check("nfc_notify", "Nachricht, wenn jemand per NFC öffnet", v.nfc_notify, "Geht an die Push- und Telegram-Empfänger dieser Tür.")}
          </div>
        </fieldset>`;
  }

  /**
   * "Keep the live picture warm" is Home Assistant's own camera setting ("preload stream"):
   * the box shows what is set there. A newly chosen camera gets it ticked as a suggestion.
   * Hidden for cameras without a stream (e.g. plain MJPEG) – nothing to keep warm there.
   */
  syncPreload(id) {
    const row = this.querySelector("[data-f=camera_preload]");
    if (!row) return;
    const st = id ? this.hass.states[id] : null;
    const canStream = !!st && ((st.attributes.supported_features || 0) & 2) !== 0;
    row.hidden = !canStream;
    if (!canStream || id === this.preloadFor) return;
    this.preloadFor = id;
    const box = row.querySelector("input");
    if (id !== this.v.camera_entity) {
      box.checked = true;
      return;
    }
    this.hass
      .callWS({ type: "camera/get_prefs", entity_id: id })
      .then((prefs) => {
        if (this.preloadFor === id) box.checked = !!prefs.preload_stream;
      })
      .catch(() => {});
  }

  async open({ hass, entryId }) {
    this.hass = hass;
    this.entryId = entryId;
    let cfg;
    try {
      cfg = await hass.callWS({ type: "portier/config" });
    } catch (err) {
      alert(`Einstellungen konnten nicht geladen werden: ${err?.message || err}`);
      this.remove();
      return;
    }
    this.cfg = cfg;
    const v = entryId ? cfg.doors[entryId] : { ...cfg.defaults, name: "" };
    this.v = v;
    this.innerHTML = this.markup(v, cfg);
    this.dialog = this.querySelector("dialog");
    this.wire();
    this.dialog.addEventListener("close", () => this.remove());
    this.dialog.showModal();
    if (!entryId) this.querySelector("[name=name]").focus();
  }

  entityField(key, label, domains, value, help = "") {
    const list = Object.values(this.hass.states)
      .filter((s) => domains.includes(s.entity_id.split(".")[0]))
      .sort((a, b) => (a.attributes.friendly_name || a.entity_id).localeCompare(b.attributes.friendly_name || b.entity_id));
    const opts = list
      .map((s) => {
        const name = s.attributes.friendly_name || s.entity_id;
        return `<li data-id="${esc(s.entity_id)}" data-q="${esc(`${name} ${s.entity_id}`.toLowerCase())}">
          <b>${esc(name)}</b><small>${esc(s.entity_id)}</small></li>`;
      })
      .join("");
    return `<div class="f" data-f="${key}">
      <span>${esc(label)}</span>
      <div class="ent">
        <div class="combo">
          <input type="text" name="${key}" value="${esc(value || "")}" autocomplete="off" spellcheck="false"
            role="combobox" aria-expanded="false" aria-label="${esc(label)}"
            placeholder="Tippen zum Suchen …">
          <button type="button" class="drop" tabindex="-1" aria-label="Liste zeigen" title="Alle zeigen">▾</button>
          <ul class="dd" role="listbox" hidden><li class="empty" data-id="" data-q="">Keine</li>${opts}<li class="none" hidden>Nichts gefunden</li></ul>
        </div>
      </div>
      <span class="ent-name"></span>
      ${help ? `<small>${help}</small>` : ""}
    </div>`;
  }

  num(key, label, value, unit, min, max, step = 1, help = "") {
    return `<label class="f" data-f="${key}"><span>${esc(label)}</span>
      <div class="inline"><input type="number" name="${key}" value="${esc(value)}" min="${min}" max="${max}" step="${step}"><span>${unit}</span></div>
      ${help ? `<small>${help}</small>` : ""}</label>`;
  }

  select(key, label, value, options, help = "") {
    return `<label class="f" data-f="${key}"><span>${esc(label)}</span>
      <select name="${key}">${options.map(([val, text]) => `<option value="${val}" ${val === value ? "selected" : ""}>${esc(text)}</option>`).join("")}</select>
      ${help ? `<small>${help}</small>` : ""}</label>`;
  }

  check(key, label, value, help = "") {
    return `<div class="f" data-f="${key}"><label class="chk"><input type="checkbox" name="${key}" ${value ? "checked" : ""}>${esc(label)}</label>
      ${help ? `<small>${help}</small>` : ""}</div>`;
  }

  markup(v, cfg) {
    // "notify" alone is HA's send-to-everyone service; it only causes duplicates here,
    // so it is shown only if a door already uses it.
    // Older versions had an "all phones" choice: a door that still uses it shows every
    // phone ticked, and saving turns that into the single phones.
    const chosen = v.notify_services || [];
    const allPhones = chosen.includes(ALL_PHONES);
    const notifyChecks = [...new Set([...cfg.notify_services.filter((x) => x !== "notify"), ...chosen])]
      .filter((svc) => svc !== ALL_PHONES)
      .map(
        (svc) => `<label class="chk"><input type="checkbox" name="notify_services" value="${esc(svc)}"
          ${chosen.includes(svc) || (allPhones && svc.startsWith("mobile_app_")) ? "checked" : ""}>${esc(notifyLabel(svc, cfg.notify_labels))}</label>`,
      )
      .join("");
    // Telegram: the chats set up in the Telegram bot integration, plus any ID already saved.
    const tgSaved = String(v.telegram_targets || "")
      .split(/[,;]/)
      .map((s) => s.trim())
      .filter((s) => /^-?\d+$/.test(s));
    const tgChats = [...(cfg.telegram_chats || []).map((c) => ({ id: String(c.id), name: c.name }))];
    for (const id of tgSaved) if (!tgChats.some((c) => c.id === id)) tgChats.push({ id, name: `Chat ${id}` });
    const telegramChecks = (cfg.telegram_chats || []).length
      ? tgChats
          .map(
            (c) => `<label class="chk"><input type="checkbox" name="telegram_chat" value="${esc(c.id)}"
          ${tgSaved.includes(c.id) ? "checked" : ""}>${esc(c.name)}</label>`,
          )
          .join("")
      : "";
    return `<dialog class="editor" aria-label="${this.entryId ? "Tür bearbeiten" : "Neue Tür"}">
    <form method="dialog" novalidate><div class="plate">
      <div class="plate-head">
        <h2>${this.entryId ? esc(v.name) : "Neue Tür"}</h2>
        <button type="button" class="x" data-act="cancel" aria-label="Schließen">×</button>
      </div>
      <div class="scroll">
        <fieldset><legend>Tür & Geräte</legend>
          <label class="f" data-f="name"><span>Name der Tür</span>
            <input type="text" name="name" value="${esc(v.name)}" placeholder="z. B. Haustür" required maxlength="60"></label>
          ${this.entityField("trigger_entity", "Klingeltaster", DOMAINS.trigger, v.trigger_entity,
            "Was beim Klingeln auslöst: Shelly-Eingang, Binärsensor, Event, Taste, Schalter oder Helfer.")}
          ${this.entityField("camera_entity", "Kamera", DOMAINS.camera, v.camera_entity)}
          <div class="cam-opts sub-block" ${v.camera_entity ? "" : "hidden"}>
          <div class="f preload" data-f="camera_preload">
            <label class="chk"><input type="checkbox" name="camera_preload" ${v.camera_preload ? "checked" : ""}>Livebild vorwärmen</label>
            <small>Hält den Kamerastream offen, damit das Livebild beim Klingeln sofort da ist.</small>
          </div>
          <div class="two" data-f="snapshot">
            ${this.select("snapshot_source", "Foto für Push und Telegram", v.snapshot_source || "auto", [
              ["auto", "Automatisch"],
              ["stream", "Aus dem Livebild"],
              ["still", "Standbild der Kamera"],
            ], "Automatisch: das Standbild der Kamera, wenn sie eines hat, sonst aus dem Livebild.")}
            ${this.num("snapshot_delay", "Foto nach dem Klingeln", v.snapshot_delay ?? 0, "s", 0, 5, 1, "Push und Telegram kommen, sobald das Foto gemacht ist. 0 = sofort.")}
          </div>
          </div>
          ${this.entityField("opener_entity", "Türöffner", DOMAINS.opener, v.opener_entity,
            "Relais, Schalter oder Helfer bekommen einen Impuls. Schloss wird geöffnet, Taste gedrückt, Skript ausgeführt.")}
          ${this.entityField("contact_entity", "Türkontakt", DOMAINS.contact, v.contact_entity, "Zeigt an, ob die Tür offen ist.")}
          ${this.entityField("gong_entity", "Gong", DOMAINS.gong, v.gong_entity, "Ein echter Gong, der bei jedem Klingeln kurz angesteuert wird.")}
        </fieldset>
        <fieldset><legend>Benachrichtigungen</legend>
          <div class="f"><span>Push-Nachricht an</span>
            <div class="checks">${notifyChecks || `<small>Keine Benachrichtigungsdienste gefunden. Installiere die Home-Assistant-App auf dem Handy.</small>`}</div>
            <small>Handys mit der Home-Assistant-App bekommen Kamerabild und Öffnen-Knopf.</small></div>
          ${telegramChecks
            ? `<div class="f" data-f="telegram_targets"><span>Telegram an</span>
            <div class="checks">${telegramChecks}</div>
            <small>Die Chats aus der Telegram-Bot-Integration. Nur ausgewählte Chats dürfen über den Knopf öffnen.</small></div>`
            : `<label class="f" data-f="telegram_targets"><span>Telegram-Chat-IDs</span>
            <input type="text" name="telegram_targets" value="${esc(v.telegram_targets || "")}" placeholder="z. B. 123456789, -100987654" inputmode="numeric">
            <small>${cfg.telegram ? "Kommagetrennt. Nur diese Chats dürfen über den Knopf öffnen." : "Die Telegram-Bot-Integration ist in Home Assistant noch nicht eingerichtet."}</small></label>`}
          ${this.check("notify_allow_open", "„Tür öffnen“-Knopf in der Nachricht", v.notify_allow_open, "Jeder Knopf öffnet nur einmal. Am Handy ist die Entsperrung nötig.")}
          <div class="two">
            ${this.num("action_window_seconds", "Knopf gültig für", v.action_window_seconds, "s", 1, 120)}
            ${this.num("notify_cooldown", "Mindestabstand", v.notify_cooldown, "s", 0, 600, 1)}
          </div>
        </fieldset>
        ${this.accessMarkup(v, cfg)}
        <fieldset><legend>Livebild & Ton</legend>
          <div class="f" data-f="sound"><span>Klingelton im Browser</span>
            <div class="inline"><select name="sound">
              ${[["dingdong", "Ding-Dong"], ["soft", "Sanft"], ["triple", "Dreiklang"], ["off", "Aus"]]
                .map(([k, t]) => `<option value="${k}" ${k === v.sound ? "selected" : ""}>${t}</option>`).join("")}
            </select><button type="button" class="quiet" data-act="preview">Anhören</button></div></div>
          <label class="f" data-f="volume"><span>Lautstärke</span>
            <div class="inline"><input type="range" name="volume" min="0" max="100" step="5" value="${esc(v.volume)}"><output>${esc(v.volume)} %</output></div></label>
          <div class="two">
            ${this.num("live_seconds", "Livebild offen lassen", v.live_seconds, "s", 5, 3600)}
            ${this.num("after_open_seconds", "Nach dem Öffnen noch", v.after_open_seconds, "s", 1, 3600)}
          </div>
          ${this.check("talk", "Gegensprechen", v.talk, "Lautsprecher- und Sprechen-Knopf im Livebild. Braucht WebRTC (z. B. go2rtc), eine Kamera, die Ton annimmt, und Home Assistant über https – sonst steht am Knopf, was fehlt.")}
        </fieldset>
        <details><summary>Feineinstellungen</summary><div>
          <p class="sub">Türöffner & Gong</p>
          ${this.select("pulse_mode", "Wer schaltet das Relais wieder aus?", v.pulse_mode, [
            ["portier", "Portier, nach der Impulsdauer"],
            ["device", "Das Relais selbst (eigener Auto-Aus-Timer)"],
          ], "„Relais selbst“ nur wählen, wenn im Gerät ein Auto-Aus eingestellt ist, z. B. Shelly „Auto Off“. Das ist am sichersten.")}
          <div class="two">
            ${this.num("pulse_seconds", "Impuls Türöffner", v.pulse_seconds, "s", 0.1, 60, 0.1)}
            ${this.num("gong_seconds", "Impuls Gong", v.gong_seconds, "s", 0.1, 30, 0.1)}
          </div>
          <p class="sub">Klingeltaster</p>
          ${this.select("trigger_edge", "Auslösen bei", v.trigger_edge, [
            ["on", "Einschalten (Standard)"], ["off", "Ausschalten (invertierter Eingang)"], ["both", "Beides"],
          ])}
          <label class="f" data-f="event_types"><span>Nur diese Event-Typen</span>
            <input type="text" name="event_types" value="${esc((v.event_types || []).join(", "))}" placeholder="z. B. single_push">
            <small>Nur für Event-Entitäten. Leer bedeutet: jeder Tastendruck.</small></label>
          ${this.num("debounce_seconds", "Entprellung", v.debounce_seconds, "s", 0, 30, 0.1, "Mehrfaches Drücken innerhalb dieser Zeit zählt als ein Klingeln.")}
        </div></details>
      </div>
      <p class="formerr" hidden></p>
      <div class="foot">
        ${this.entryId ? `<button type="button" class="danger" data-act="delete">Tür entfernen</button>` : ""}
        <span class="grow"></span>
        <button type="button" class="quiet" data-act="cancel">Abbrechen</button>
        <button type="submit" class="primary">${this.entryId ? "Speichern" : "Tür anlegen"}</button>
      </div>
    </div></form></dialog>`;
  }

  wire() {
    const q = (s) => this.querySelector(s);
    this.querySelectorAll("[data-act=cancel]").forEach((b) => b.addEventListener("click", () => this.dialog.close()));
    this.dialog.addEventListener("cancel", () => this.dialog.close());
    // Show the friendly name below each entity field.
    this.querySelectorAll(".ent input").forEach((inp) => {
      const out = inp.closest(".f").querySelector(".ent-name");
      const update = () => {
        const id = inp.value.trim();
        const st = this.hass.states[id];
        out.textContent = !id ? "" : st ? `✓ ${st.attributes.friendly_name || id}` : "Nicht gefunden";
        out.classList.toggle("missing", !!id && !st);
        if (inp.name === "camera_entity") {
          // The camera's own settings show only with a camera.
          const opts = this.querySelector(".cam-opts");
          if (opts) opts.hidden = !id;
          this.syncPreload(id);
        }
      };
      const field = inp.closest(".f");
      const dd = field.querySelector(".dd");
      const items = [...dd.querySelectorAll("li[data-id]")];
      const none = dd.querySelector(".none");
      let hi = null;
      const shown = () => items.filter((li) => !li.hidden);
      const mark = (li, scroll = true) => {
        hi?.classList.remove("hi");
        hi = li;
        if (li) {
          li.classList.add("hi");
          if (scroll) li.scrollIntoView({ block: "nearest" });
        }
      };
      // The editor lives in the panel's shadow root: document.activeElement is the panel there.
      const focused = () => inp.getRootNode().activeElement === inp;
      // Show the list: everything (arrow, click, focus) or only what matches the typed text.
      const open = (filter) => {
        const words = (filter || "").toLowerCase().split(/\s+/).filter(Boolean);
        const cur = inp.value.trim();
        for (const li of items) {
          li.hidden = !words.every((w) => li.dataset.q.includes(w));
          li.classList.toggle("cur", li.dataset.id === cur);
        }
        none.hidden = shown().length > 0;
        dd.hidden = false;
        inp.setAttribute("aria-expanded", "true");
        mark(shown().find((li) => li.dataset.id === cur) || (words.length ? shown()[0] : null));
      };
      const close = () => {
        dd.hidden = true;
        inp.setAttribute("aria-expanded", "false");
        mark(null);
      };
      const pick = (li) => {
        inp.value = li.dataset.id;
        update();
        close();
      };
      inp.addEventListener("input", () => {
        update();
        open(inp.value);
      });
      // Clicking into a filled field selects its text (typing replaces it) and
      // lists every choice, not only the current one.
      inp.addEventListener("focus", () => {
        setTimeout(() => inp.select(), 0);
        open("");
      });
      // Clicking the field toggles the list like any dropdown. The first click only focuses
      // (focus opens the list), so remember before the click whether it was open already.
      let wasOpen = false;
      inp.addEventListener("mousedown", () => (wasOpen = focused() && !dd.hidden));
      inp.addEventListener("click", () => {
        if (wasOpen) close();
        else if (dd.hidden) open("");
        wasOpen = false;
      });
      inp.addEventListener("blur", () => setTimeout(() => !focused() && close(), 150));
      inp.addEventListener("keydown", (e) => {
        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
          e.preventDefault();
          if (dd.hidden) return open("");
          const list = shown();
          const i = list.indexOf(hi);
          mark(list[e.key === "ArrowDown" ? Math.min(i + 1, list.length - 1) : Math.max(i - 1, 0)] || null);
        } else if (e.key === "Enter" && !dd.hidden) {
          e.preventDefault();
          if (hi) pick(hi);
          else close();
        } else if (e.key === "Escape" && !dd.hidden) {
          e.preventDefault();
          e.stopPropagation();
          close();
        }
      });
      // mousedown keeps the focus in the field, so blur does not close the list first.
      dd.addEventListener("mousedown", (e) => e.preventDefault());
      dd.addEventListener("click", (e) => {
        const li = e.target.closest("li[data-id]");
        if (li) pick(li);
      });
      // The entry under the mouse is the marked one (Enter picks it).
      dd.addEventListener("mouseover", (e) => {
        const li = e.target.closest("li[data-id]");
        if (li && li !== hi) mark(li, false);
      });
      const drop = field.querySelector(".drop");
      drop.addEventListener("mousedown", (e) => e.preventDefault());
      drop.addEventListener("click", () => {
        if (!dd.hidden) return close();
        if (!focused()) inp.focus();
        open("");
      });
      update();
    });
    // NFC fields only while the switch is on.
    const nfcOn = q("[name=nfc_on]");
    nfcOn?.addEventListener("change", () => (q(".nfc-body").hidden = !nfcOn.checked));
    const vol = q("[name=volume]");
    vol.addEventListener("input", () => (vol.nextElementSibling.value = `${vol.value} %`));
    q("[data-act=preview]").addEventListener("click", (e) =>
      soundButton(e.currentTarget, q("[name=sound]").value, Number(vol.value)),
    );
    const del = q("[data-act=delete]");
    del?.addEventListener("click", async () => {
      if (!del.classList.contains("armed")) {
        del.classList.add("armed");
        del.textContent = "Wirklich entfernen?";
        setTimeout(() => {
          del.classList.remove("armed");
          del.textContent = "Tür entfernen";
        }, 4000);
        return;
      }
      del.disabled = true;
      try {
        await this.hass.callWS({ type: "portier/door/delete", entry_id: this.entryId });
        ctrl.doors.delete(this.entryId);
        ctrl.listeners.forEach((fn) => fn({ type: "local" }));
        this.dialog.close();
      } catch (err) {
        this.showError({ base: err?.message || "Entfernen fehlgeschlagen" });
        del.disabled = false;
      }
    });
    q("form").addEventListener("submit", (e) => {
      e.preventDefault();
      this.save();
    });
  }

  collect() {
    const f = this.querySelector("form");
    const val = (n) => f.elements[n]?.value?.trim?.() ?? "";
    const num = (n) => Number(val(n).replace(",", "."));
    return {
      name: val("name"),
      trigger_entity: val("trigger_entity"),
      camera_entity: val("camera_entity") || null,
      opener_entity: val("opener_entity") || null,
      contact_entity: val("contact_entity") || null,
      gong_entity: val("gong_entity") || null,
      notify_services: [...f.querySelectorAll("[name=notify_services]:checked")].map((c) => c.value),
      telegram_targets: f.querySelector("[name=telegram_chat]")
        ? [...f.querySelectorAll("[name=telegram_chat]:checked")].map((c) => c.value).join(", ")
        : val("telegram_targets"),
      notify_allow_open: f.elements.notify_allow_open.checked,
      action_window_seconds: num("action_window_seconds"),
      notify_cooldown: num("notify_cooldown"),
      sound: val("sound"),
      volume: num("volume"),
      live_seconds: num("live_seconds"),
      after_open_seconds: num("after_open_seconds"),
      pulse_mode: val("pulse_mode"),
      pulse_seconds: num("pulse_seconds"),
      gong_seconds: num("gong_seconds"),
      open_admin_only: f.elements.open_admin_only.checked,
      talk: f.elements.talk.checked,
      camera_preload: f.elements.camera_preload.checked,
      snapshot_source: val("snapshot_source"),
      snapshot_delay: num("snapshot_delay"),
      trigger_edge: val("trigger_edge"),
      event_types: val("event_types"),
      debounce_seconds: num("debounce_seconds"),
      nfc_tags: f.elements.nfc_on.checked ? [...f.querySelectorAll("[name=nfc_tags]:checked")].map((c) => c.value) : [],
      nfc_devices: [...f.querySelectorAll("[name=nfc_devices]:checked")].map((c) => c.value),
      nfc_from: val("nfc_from") || null,
      nfc_until: val("nfc_until") || null,
      nfc_notify: f.elements.nfc_notify.checked,
    };
  }

  showError(errors) {
    this.querySelectorAll(".f.bad").forEach((el) => el.classList.remove("bad"));
    this.querySelectorAll("small.err").forEach((el) => el.remove());
    const general = [];
    for (const [key, msg] of Object.entries(errors)) {
      const text = ERRORS[msg] || msg;
      const field = this.querySelector(`[data-f="${key}"]`);
      if (field) {
        field.classList.add("bad");
        const small = document.createElement("small");
        small.className = "err";
        small.textContent = text;
        field.append(small);
        field.closest("details")?.setAttribute("open", "");
      } else general.push(text);
    }
    const box = this.querySelector(".formerr");
    box.hidden = !general.length && !Object.keys(errors).length;
    box.textContent = general.length ? general.join(" ") : "Bitte die markierten Felder prüfen.";
    this.querySelector(".bad input, .bad select")?.focus();
  }

  async save() {
    const data = this.collect();
    const local = {};
    if (!data.name) local.name = "Bitte einen Namen eingeben.";
    for (const k of ["trigger_entity", "camera_entity", "opener_entity", "contact_entity", "gong_entity"]) {
      if (data[k] && !this.hass.states[data[k]]) local[k] = "Diese Entität gibt es nicht.";
    }
    if (Object.keys(local).length) return this.showError(local);
    const btn = this.querySelector(".primary");
    btn.disabled = true;
    btn.textContent = "Speichert …";
    try {
      await this.hass.callWS({ type: "portier/door/save", options: data, ...(this.entryId ? { entry_id: this.entryId } : {}) });
      this.dialog.close();
    } catch (err) {
      let errors;
      try {
        errors = JSON.parse(err?.message);
      } catch {
        errors = { base: err?.message || "Speichern fehlgeschlagen" };
      }
      this.showError(errors);
      btn.disabled = false;
      btn.textContent = this.entryId ? "Speichern" : "Tür anlegen";
    }
  }
}

/* -------------------------------------------------------------------- panel */

// Live views open on the Türklingel page; HA builds a new panel when you come back from
// another page, which then starts them again by itself. They end by themselves after
// 5 minutes (a camera that should always run can be shown on a dashboard instead).
const PANEL_LIVE_MS = 5 * 60 * 1000;
const panelLive = new Map(); // entry_id -> time the live view ends

class PortierPanel extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this.cards = new Map();
    this.live = new Map(); // entry_id -> CameraView
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `<style>${CSS}
      :host{display:block;min-height:100%;background:var(--primary-background-color,#1c1f22);
        color:var(--primary-text-color,#e8e8e8)}
      .bar{display:flex;align-items:center;gap:8px;height:56px;padding:0 8px 0 4px;
        background:var(--app-header-background-color,var(--primary-color));color:var(--app-header-text-color,#fff)}
      .bar h1{font-size:20px;font-weight:400;margin:0 0 0 8px}
      .bar .ver{font-size:12px;opacity:.6;margin-left:6px;white-space:nowrap;padding-top:4px}
      .menu{border:0;background:none;width:48px;height:48px;border-radius:50%;color:inherit;display:grid;place-items:center}
      main{max-width:1180px;margin:0 auto;padding:20px 16px 48px;display:grid;gap:20px}
      .doors{display:grid;gap:20px;grid-template-columns:repeat(auto-fill,minmax(min(100%,420px),1fr))}
      .meta{padding:14px 18px 0;display:flex;flex-wrap:wrap;gap:6px 18px;font-size:13px;color:var(--pt-muted)}
      .meta b{color:var(--pt-ink);font-weight:500}
      .actions{display:flex;gap:8px;flex-wrap:wrap}
      details{border-top:1px solid var(--pt-edge);margin:0 18px;padding:10px 0 14px}
      summary{cursor:pointer;font-size:14px;color:var(--pt-muted)}
      /* time · way · name as aligned columns; names line up on the right in both lists */
      ol{list-style:none;margin:10px 0 0;padding:0;display:grid;grid-template-columns:minmax(8.6em,max-content) minmax(0,13em) minmax(6.5em,max-content);justify-content:start;
        gap:4px 12px;font-size:14px;font-variant-numeric:tabular-nums}
      ol li{display:contents;color:var(--pt-ink)}
      ol li b{font-weight:600}
      ol li span{color:var(--pt-muted);min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
      ol li.empty span{grid-column:1/-1;white-space:normal}
      @media (max-width:520px){
        ol{grid-template-columns:max-content minmax(0,1fr) max-content;column-gap:10px}
        ol li span{white-space:normal;overflow-wrap:break-word}
      }
      details[hidden]{display:none}
      ${PICTURE_CSS}
      .live-btn{flex:none;border:0;border-radius:999px;padding:6px 12px;white-space:nowrap;
        background:#0000004d;backdrop-filter:blur(6px);color:#fff;font-size:13px}
      .live-btn:hover{background:#00000080}
      .live-btn.on{color:var(--pt-brass-hi);box-shadow:inset 0 0 0 1px var(--pt-brass)}
      .pic .status{display:grid;gap:8px}
      .pic .meta{padding:0;gap:2px 14px;font-size:12px;color:#ffffffd9}
      .pic .meta b{color:#fff}
      .pic .quiet{color:#fff;border-color:#ffffff59;background:#0000004d;backdrop-filter:blur(6px);
        padding:5px 12px;font-size:13px;text-shadow:none}
      .pic .quiet:hover{background:#00000080}
      .pic details{border-top:0;margin:0 16px}
      .device{background:var(--card-background-color,#24282c);color:var(--primary-text-color);
        border:1px solid var(--divider-color,#3c4248);border-radius:16px;padding:16px 18px;display:grid;gap:12px}
      .device h2{font-size:16px;margin:0;font-weight:600}
      .device p{margin:0;font-size:13px;color:var(--secondary-text-color,#9aa0a6);max-width:70ch}
      .row{display:flex;align-items:center;justify-content:space-between;gap:16px;font-size:14px}
      .switch{appearance:none;width:42px;height:24px;border-radius:999px;background:var(--divider-color,#555);position:relative;cursor:pointer;flex:none;margin:0}
      .switch::after{content:"";position:absolute;top:3px;left:3px;width:18px;height:18px;border-radius:50%;background:#fff;transition:transform .15s}
      .switch:checked{background:var(--pt-brass)}
      .switch:checked::after{transform:translateX(18px)}
      .switch:focus-visible{outline:2px solid var(--pt-brass);outline-offset:2px}
      .device .quiet{color:var(--primary-text-color);border-color:var(--divider-color,#555)}
      .quiet.working,.pic .quiet.working,.device .quiet.working{color:var(--pt-brass-hi);border-color:var(--pt-brass);cursor:default}
      .quiet.failed,.pic .quiet.failed,.device .quiet.failed{color:var(--pt-alert);border-color:var(--pt-alert);cursor:default}
      .empty{text-align:center;padding:48px 16px;color:var(--secondary-text-color);display:grid;gap:16px;justify-items:center}
      .bar .spacer{flex:1}
      .add{border:0;border-radius:999px;padding:8px 16px;background:var(--pt-brass);color:var(--pt-on-brass);font-weight:600;font-size:14px}
      .add:hover{background:var(--pt-brass-hi)}
      .device{max-width:720px}
      .device select{font:inherit;font-size:14px;color:var(--primary-text-color);background:var(--secondary-background-color,#1a1d20);
        border:1px solid var(--divider-color,#555);border-radius:10px;padding:6px 10px;max-width:60%}
      .device input[type=color]{width:56px;height:32px;border:1px solid var(--divider-color,#555);border-radius:8px;background:none;padding:2px;cursor:pointer;flex:none}
      .device .row[hidden],.device[hidden]{display:none}
      .look-state{font-size:13px;color:var(--secondary-text-color,#9aa0a6)}
      .gear{border:0;background:none;color:var(--pt-muted);width:36px;height:36px;border-radius:50%;display:grid;place-items:center}
      .gear:hover{color:var(--pt-ink);background:#ffffff10}
      ${EDITOR_CSS}
    </style>
    <div class="bar"><button class="menu" aria-label="Menü"><svg width="24" height="24" viewBox="0 0 24 24"><path fill="currentColor" d="M3 6h18v2H3V6m0 5h18v2H3v-2m0 5h18v2H3v-2Z"/></svg></button><h1>Türklingel</h1><span class="ver" title="Installierte Portier-Version">Portier ${VERSION}</span><span class="spacer"></span><button class="add" data-act="add" hidden>+ Tür hinzufügen</button></div>
    <main>
      <div class="doors"></div>
      <section class="device">
        <h2>Dieses Gerät</h2>
        <p>Gilt nur für diesen Browser. Push aufs Handy und Telegram stellst du in den Einstellungen der Tür ein.</p>
        <label class="row">Livebild einblenden, wenn es klingelt<input type="checkbox" class="switch" data-k="overlay"></label>
        <label class="row">Klingelton abspielen<input type="checkbox" class="switch" data-k="sound"></label>
        <div class="row"><span class="sound-state"></span><button class="quiet" data-act="sound-test">Ton testen</button></div>
      </section>
      <section class="device look" hidden>
        <h2>Farben</h2>
        <p>Gilt für alle Geräte. Hauptfarbe: Öffnen-Knopf, Lampe, Balken, Schalter und Knöpfe. Zweitfarbe: Bestätigungen wie „Tür geöffnet“.</p>
        <label class="row">Farben
          <select data-look="mode">
            <option value="default">Standard (Messing und Grün)</option>
            <option value="system">Wie System (Home-Assistant-Design)</option>
            <option value="custom">Eigene Farben</option>
          </select></label>
        <label class="row" data-custom>Hauptfarbe<input type="color" data-look="accent"></label>
        <label class="row" data-custom>Zweitfarbe<input type="color" data-look="accent2"></label>
        <span class="look-state"></span>
      </section>
    </main>`;
    this.$ = (s) => root.querySelector(s);
    this.$(".menu").addEventListener("click", () =>
      this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true })),
    );
    const settings = deviceSettings();
    root.querySelectorAll(".switch").forEach((sw) => {
      sw.checked = settings[sw.dataset.k];
      sw.addEventListener("change", () => saveDeviceSettings({ ...deviceSettings(), [sw.dataset.k]: sw.checked }));
    });
    this.$("[data-act=sound-test]").addEventListener("click", async (e) => {
      const door = [...ctrl.doors.values()][0];
      await soundButton(e.currentTarget, door?.sound || "dingdong", door?.volume ?? 70);
      this.renderSoundState();
    });
    this.$("[data-act=add]").addEventListener("click", () => this.edit(null));
    this.wireLook();
    this.onMsg = () => this.render();
  }

  /** The "Farben" box: one setting for every device, saved on the server. */
  wireLook() {
    const box = this.$(".look");
    const field = (k) => box.querySelector(`[data-look=${k}]`);
    const state = box.querySelector(".look-state");
    const current = () => ({
      mode: field("mode").value,
      accent: field("accent").value,
      accent2: field("accent2").value,
    });
    const save = async (look) => {
      state.textContent = "Speichert …";
      try {
        ctrl.look = await (this._hass || rootHass()).callWS({ type: "portier/look/save", ...look });
        state.textContent = "Gespeichert – gilt auf allen Geräten.";
      } catch (err) {
        state.textContent = `Nicht gespeichert: ${err?.message || err}`;
      }
      this.renderLook();
    };
    field("mode").addEventListener("change", () => save(current()));
    for (const k of ["accent", "accent2"]) {
      // Show the color right away while picking; save when the picker is closed.
      field(k).addEventListener("input", () => {
        applyLook(this, current());
        applyLook(ctrl.overlay, current());
      });
      field(k).addEventListener("change", () => save(current()));
    }
  }

  renderLook() {
    const look = ctrl.look || LOOK_DEFAULT;
    applyLook(this, look);
    const box = this.$(".look");
    box.hidden = !this.isAdmin;
    const field = (k) => box.querySelector(`[data-look=${k}]`);
    // Do not overwrite a color while it is being picked.
    const active = this.shadowRoot.activeElement;
    field("mode").value = look.mode;
    if (active !== field("accent")) field("accent").value = toHex(look.accent) || LOOK_DEFAULT.accent.toLowerCase();
    if (active !== field("accent2")) field("accent2").value = toHex(look.accent2) || LOOK_DEFAULT.accent2.toLowerCase();
    for (const row of box.querySelectorAll("[data-custom]")) row.hidden = look.mode !== "custom";
  }

  get isAdmin() {
    return !!(this._hass || rootHass())?.user?.is_admin;
  }

  async edit(entryId) {
    const editor = document.createElement("portier-editor");
    this.shadowRoot.append(editor);
    editor.open({ hass: this._hass || rootHass(), entryId });
  }

  set hass(h) {
    this._hass = h;
    if (!this.cards.size && ctrl.doors.size) this.render();
    this.$("[data-act=add]").hidden = !h?.user?.is_admin;
    this.renderLook(); // also follows a changed Home Assistant theme ("Wie System")
    if (this.pendingEdit && ctrl.doors.size) this.applyPending();
    // HA hands out a new camera key every few minutes and after a reconnect: load the
    // still with it right away instead of keeping a picture that failed with the old one.
    for (const [id, card] of this.cards) {
      const door = ctrl.doors.get(id);
      if (door && !this.live.has(id) && this.stillPic(door) !== card.stillPic) this.refreshStill(card, door, true);
    }
  }

  set narrow(n) {
    this.$(".menu").style.display = n ? "" : "none";
  }

  connectedCallback() {
    ctrl.listeners.add(this.onMsg);
    this.readParams();
    // HA navigates inside the page (e.g. the link in the setup dialog) without rebuilding the panel.
    this.onLocation = () => {
      if (location.pathname.replace(/\/$/, "") !== "/portier") return;
      this.readParams();
      if (ctrl.doors.size) this.applyPending();
    };
    window.addEventListener("location-changed", this.onLocation);
    window.addEventListener("popstate", this.onLocation);
    this.render();
    this.ticker = setInterval(() => this.refresh(), 10000);
    // Back from the background (HA rebuilds the page after 5 min hidden): fresh stills now.
    this.onVisible = () => {
      if (document.visibilityState !== "visible") return;
      for (const [id, card] of this.cards) {
        const door = ctrl.doors.get(id);
        if (door && !this.live.has(id)) this.refreshStill(card, door, true);
      }
    };
    document.addEventListener("visibilitychange", this.onVisible);
  }

  /** /portier?show=<entry_id> (push notification) shows that door's live picture;
   *  /portier?edit=<entry_id|new> (setup dialog, "Configure") opens the door editor. */
  readParams() {
    const params = new URLSearchParams(location.search);
    let changed = false;
    for (const key of ["show", "edit"]) {
      if (!params.has(key)) continue;
      this[key === "show" ? "pendingShow" : "pendingEdit"] = params.get(key);
      params.delete(key);
      changed = true;
    }
    if (changed) {
      const rest = params.toString();
      history.replaceState(history.state, "", location.pathname + (rest ? `?${rest}` : "") + location.hash);
    }
  }

  applyPending() {
    if (this.pendingShow && ctrl.doors.has(this.pendingShow)) {
      const id = this.pendingShow;
      this.pendingShow = null;
      if (!ctrl.overlay?.views.has(id)) ctrl.show(id, { manual: true });
    }
    if (this.pendingEdit && this.isAdmin) {
      const doors = [...ctrl.doors.values()];
      // "new" = the door just added in the setup dialog: the newest one without any device yet.
      const door =
        this.pendingEdit === "new"
          ? [...doors].reverse().find((d) => !d.camera && !d.opener && !d.contact) || doors.at(-1)
          : ctrl.doors.get(this.pendingEdit);
      this.pendingEdit = null;
      if (door && !this.shadowRoot.querySelector("portier-editor")) this.edit(door.entry_id);
    }
  }

  disconnectedCallback() {
    window.removeEventListener("location-changed", this.onLocation);
    window.removeEventListener("popstate", this.onLocation);
    ctrl.listeners.delete(this.onMsg);
    document.removeEventListener("visibilitychange", this.onVisible);
    clearInterval(this.ticker);
    // Leaving the page ends the stream; panelLive remembers it for the way back.
    for (const v of this.live.values()) v.destroy();
    this.live.clear();
    this.cards.clear();
  }

  renderSoundState() {
    this.$(".sound-state").textContent = ctrl.chime.ready
      ? "Ton ist in diesem Browser freigeschaltet."
      : "Ton wird beim ersten Tippen auf die Seite freigeschaltet.";
  }

  render() {
    this.renderLook();
    const box = this.$(".doors");
    const doors = [...ctrl.doors.values()];
    if (!doors.length) {
      box.innerHTML = `<div class="empty"><span>Noch keine Tür eingerichtet.</span>
        ${this.isAdmin ? `<button class="add">+ Erste Tür hinzufügen</button>` : `<span>Ein Administrator kann hier Türen anlegen.</span>`}</div>`;
      box.querySelector(".add")?.addEventListener("click", () => this.edit(null));
      for (const v of this.live.values()) v.destroy();
      this.live.clear();
      this.cards.clear();
      return;
    }
    if (box.querySelector(".empty")) box.innerHTML = "";
    // A door briefly disappears while it reloads after saving; only remove its card
    // if it is still gone a few seconds later.
    for (const id of [...this.cards.keys()]) {
      if (ctrl.doors.has(id)) continue;
      if (this.removing?.has(id)) continue;
      (this.removing ??= new Map()).set(
        id,
        setTimeout(() => {
          this.removing.delete(id);
          if (ctrl.doors.has(id)) return;
          this.cards.get(id)?.remove();
          this.cards.delete(id);
          this.live.get(id)?.destroy();
          panelLive.delete(id);
          this.live.delete(id);
        }, 4000),
      );
    }
    // Keep the order of the doors stable (alphabetical), whatever order updates arrive in.
    const sorted = doors.slice().sort((a, b) => a.name.localeCompare(b.name, "de"));
    sorted.forEach((door, index) => {
      if (this.removing?.has(door.entry_id)) {
        clearTimeout(this.removing.get(door.entry_id));
        this.removing.delete(door.entry_id);
      }
      let card = this.cards.get(door.entry_id);
      const isNew = !card;
      if (isNew) {
        card = this.buildCard(door);
        this.cards.set(door.entry_id, card);
      }
      // Move only cards that are out of place, so a running live view is not interrupted.
      if (box.children[index] !== card) box.insertBefore(card, box.children[index] || null);
      this.fill(card, door);
      if (isNew && panelLive.has(door.entry_id) && door.camera && !this.live.has(door.entry_id)) {
        this.toggleLive(door.entry_id, card, true);
      }
    });
    this.renderSoundState();
    this.applyPending();
  }

  buildCard(door) {
    const el = document.createElement("article");
    el.className = "plate pic";
    el.innerHTML = `
      <div class="window">
        <img class="still" alt="">
        <div class="plate-head">
        <span class="lamp"></span>
        <h2 class="door-name"></h2>
        <button class="live-btn">Live ansehen</button>
        <button class="gear" data-act="edit" title="Tür bearbeiten" aria-label="Tür bearbeiten"><svg width="20" height="20" viewBox="0 0 24 24"><path fill="currentColor" d="M12 15.5A3.5 3.5 0 1 1 12 8.5a3.5 3.5 0 0 1 0 7m7.43-2.53c.04-.32.07-.64.07-.97s-.03-.66-.07-1l2.11-1.63c.19-.15.24-.42.12-.64l-2-3.46c-.12-.22-.39-.31-.61-.22l-2.49 1c-.52-.39-1.06-.73-1.69-.98l-.37-2.65A.506.506 0 0 0 14 2h-4c-.25 0-.46.18-.5.42l-.37 2.65c-.63.25-1.17.59-1.69.98l-2.49-1c-.22-.09-.49 0-.61.22l-2 3.46c-.13.22-.07.49.12.64L4.57 11c-.04.34-.07.67-.07 1s.03.65.07.97l-2.11 1.66c-.19.15-.25.42-.12.64l2 3.46c.12.22.39.3.61.22l2.49-1.01c.52.4 1.06.74 1.69.99l.37 2.65c.04.24.25.42.5.42h4c.25 0 .46-.18.5-.42l.37-2.65c.63-.26 1.17-.59 1.69-.99l2.49 1.01c.22.08.49 0 .61-.22l2-3.46c.12-.22.07-.49-.12-.64l-2.11-1.66Z"/></svg></button>
        </div>
        <div class="controls">
          <div class="status">
            <div class="meta"></div>
            <div class="actions"><button class="quiet" data-act="test">Testklingel</button></div>
          </div>
          ${talkMarkup()}
          ${keyMarkup(door.can_open)}
        </div>
      </div>
      <details class="rings"><summary>Klingelverlauf</summary><ol></ol></details>
      <details class="opens"><summary>Türöffner-Verlauf</summary><ol></ol></details>`;
    el.querySelector(".live-btn").addEventListener("click", () => this.toggleLive(door.entry_id, el));
    // A still that failed (old camera key, connection just back): try again shortly
    // with whatever key HA has by then, without waiting for the next refresh.
    const still = el.querySelector(".still");
    still.addEventListener("load", () => (el.stillFails = 0));
    still.addEventListener("error", () => {
      if (!still.getAttribute("src") || still.src.startsWith("data:")) return;
      // From now on take this door's still from Portier (works where HA's fails).
      if (!el.portierStill) {
        el.portierStill = true;
        const d = ctrl.doors.get(door.entry_id);
        if (d && el.isConnected && !this.live.has(door.entry_id)) this.refreshStill(el, d, true);
        return;
      }
      el.stillFails = (el.stillFails || 0) + 1;
      clearTimeout(el.stillRetry);
      el.stillRetry = setTimeout(() => {
        const d = ctrl.doors.get(door.entry_id);
        if (d && el.isConnected && !this.live.has(door.entry_id)) this.refreshStill(el, d, true);
      }, Math.min(2000 * 2 ** (el.stillFails - 1), 30000));
    });
    const gear = el.querySelector("[data-act=edit]");
    gear.hidden = !this.isAdmin;
    gear.addEventListener("click", () => this.edit(door.entry_id));
    const test = el.querySelector("[data-act=test]");
    test.addEventListener("click", () => {
      if (test.disabled) return;
      test.disabled = true;
      test.classList.add("working");
      test.textContent = "Klingelt …";
      ctrl
        .testRing(door.entry_id)
        .catch((err) => {
          console.warn("Portier: Testklingel fehlgeschlagen", err);
          test.classList.replace("working", "failed");
          test.textContent = "Fehlgeschlagen";
        })
        .finally(() =>
          setTimeout(() => {
            test.disabled = false;
            test.classList.remove("working", "failed");
            test.textContent = "Testklingel";
          }, 2000),
        );
    });
    if (door.can_open) wireKey(el.querySelector(".key"), el.querySelector(".key-label"), () => ctrl.open(door.entry_id));
    return el;
  }

  toggleLive(entryId, el, restore = false) {
    const btn = el.querySelector(".live-btn");
    const existing = this.live.get(entryId);
    el.talk ??= wireTalk(el, () => this.live.get(entryId) || null);
    if (existing) {
      el.talk.reset();
      existing.destroy();
      this.live.delete(entryId);
      panelLive.delete(entryId);
      btn.classList.remove("on");
      btn.textContent = "Live ansehen";
      const d = ctrl.doors.get(entryId);
      if (d && el.isConnected) this.refreshStill(el, d, true);
      return;
    }
    // A live view started here ends by itself after PANEL_LIVE_MS; coming back to the
    // page restores it only for the time that is left.
    const until = restore ? panelLive.get(entryId) || 0 : Date.now() + PANEL_LIVE_MS;
    if (until <= Date.now()) {
      panelLive.delete(entryId);
      return;
    }
    const door = ctrl.doors.get(entryId);
    const view = new CameraView(door.camera, () => this._hass || rootHass(), {
      entryId,
      talk: !!door.talk,
      onTalk: el.talk.onTalk,
    });
    const win = el.querySelector(".window");
    win.insertBefore(view.el, win.querySelector(".plate-head"));
    view.start();
    this.live.set(entryId, view);
    panelLive.set(entryId, until);
    setTimeout(() => {
      // Only if this very view is still running (not stopped, replaced or left).
      if (this.live.get(entryId) === view) this.toggleLive(entryId, el);
    }, until - Date.now());
    btn.classList.add("on");
    btn.textContent = "Live beenden";
  }

  stateText(entityId) {
    const s = entityId && this._hass?.states[entityId];
    if (!s) return null;
    const map = {
      on: "offen", off: "zu", open: "offen", closed: "zu", opening: "öffnet", closing: "schließt",
      locked: "verriegelt", unlocked: "entriegelt", open_lock: "offen", unavailable: "nicht erreichbar",
    };
    return map[s.state] || s.state;
  }

  fill(card, door) {
    card.querySelector(".door-name").textContent = door.name;
    card.querySelector(".lamp").classList.toggle("on", !!door.last_ring && ctrl.now() - new Date(door.last_ring) < 60000);
    const contact = this.stateText(door.contact);
    const opener = door.opener ? this.stateText(door.opener) : null;
    card.querySelector(".meta").innerHTML = [
      `Zuletzt geklingelt <b>${esc(relative(door.last_ring, ctrl.now()))}</b>`,
      contact ? `Tür <b>${esc(contact)}</b>` : "",
      door.last_opened ? `Zuletzt geöffnet <b>${esc(relative(door.last_opened, ctrl.now()))}</b>` : "",
      opener && ["on", "unlocked", "open"].includes(this._hass?.states[door.opener]?.state) ? `Öffner <b>aktiv</b>` : "",
    ]
      .filter(Boolean)
      .map((t) => `<span>${t}</span>`)
      .join("");
    const list = card.querySelector(".rings ol");
    list.innerHTML = door.history.length
      ? door.history
          .map(
            (r) =>
              histRow(when(r.time), ...ringWho(r)),
          )
          .join("")
      : `<li class="empty"><span>Noch hat niemand geklingelt.</span></li>`;
    card.querySelector(".rings summary").textContent = `Klingelverlauf (${door.history.length})`;
    // Who opened the door, and which way.
    const opens = door.opens || [];
    const openBox = card.querySelector(".opens");
    openBox.hidden = !door.opener;
    openBox.querySelector("ol").innerHTML = opens.length
      ? opens
          .map(
            (r) =>
              histRow(when(r.time), openWho(r), OPEN_VIA[r.via] || r.via),
          )
          .join("")
      : `<li class="empty"><span>Noch nie über Portier geöffnet.</span></li>`;
    openBox.querySelector("summary").textContent = `Türöffner-Verlauf (${opens.length})`;
    if (!this.live.has(door.entry_id)) this.refreshStill(card, door);
    const live = card.querySelector(".live-btn");
    live.hidden = !door.camera;
  }

  stillPic(door) {
    return (door.camera && (this._hass || rootHass())?.states[door.camera]?.attributes?.entity_picture) || "";
  }

  refreshStill(card, door, force = false) {
    const img = card.querySelector(".still");
    const pic = this.stillPic(door);
    card.stillPic = pic;
    // Home Assistant's still did not work for this camera: Portier takes it (every 10 s at most).
    if (pic && card.portierStill) {
      const now = Date.now();
      if (!force && now - (card.portierStillAt || 0) < 10000) return;
      card.portierStillAt = now;
      ctrl.portierStill(door.entry_id).then((url) => {
        if (url && card.isConnected && !this.live.has(door.entry_id)) img.src = url;
      });
      return;
    }
    if (pic) {
      const stamp = force ? Date.now() : Math.floor(Date.now() / 10000);
      const src = `${pic}${pic.includes("?") ? "&" : "?"}t=${stamp}`;
      if (force || img.getAttribute("src") !== src) {
        img.src = src;
        // A still that takes this long will fail or come too late (HA waits ~8 s for
        // cameras it cannot read): take this door's still from Portier from now on.
        clearTimeout(card.stillSlow);
        card.stillSlow = setTimeout(() => {
          if (!card.isConnected || this.live.has(door.entry_id)) return;
          if (img.getAttribute("src") === src && !(img.complete && img.naturalWidth)) {
            card.portierStill = true;
            this.refreshStill(card, door, true);
          }
        }, 5000);
      }
    } else {
      img.removeAttribute("src");
    }
  }

  refresh() {
    for (const [id, card] of this.cards) {
      const door = ctrl.doors.get(id);
      if (door) this.fill(card, door);
    }
  }
}

/* --------------------------------------------------------------------- boot */

// After an update the browser may still run the previous version (HA restarts don't
// reload the page). Mixing versions breaks things, so reload once to get the new one.
if (window.__portier && window.__portier.version !== VERSION) {
  const key = `portier.reloaded.${VERSION}`;
  let already = false;
  try {
    already = sessionStorage.getItem(key) === "1";
    sessionStorage.setItem(key, "1");
  } catch {
    /* ignore */
  }
  if (!already) location.reload();
}
const ctrl = (window.__portier ??= new PortierController());
ctrl.version ??= VERSION;
if (!customElements.get("portier-overlay")) customElements.define("portier-overlay", PortierOverlay);
if (!customElements.get("portier-editor")) customElements.define("portier-editor", PortierEditor);
if (!customElements.get("portier-panel")) customElements.define("portier-panel", PortierPanel);
console.info(`%c Portier %c ${VERSION} `, "background:#C9A44C;color:#24282C;font-weight:600", "background:#24282C;color:#F2EFE6");

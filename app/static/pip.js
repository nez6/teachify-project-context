// Pip: the buddy character. Pure SVG, no assets. mountPip(el) returns controls.
const SVG = `
<svg viewBox="0 0 220 240" role="img" aria-label="Pip, a round tomato-red buddy with a pencil tuft">
  <defs>
    <clipPath id="pipBody"><path d="M110 40c52 0 88 34 88 92 0 58-38 92-88 92s-88-34-88-92c0-58 36-92 88-92z"/></clipPath>
  </defs>
  <ellipse class="pip-shadow" cx="110" cy="230" rx="62" ry="8" fill="#20243a" opacity=".12"/>
  <g class="pip-all">
    <g class="pip-tuft">
      <path d="M110 46 L100 18 L120 18 Z" fill="#ffc53d" stroke="#20243a" stroke-width="4" stroke-linejoin="round"/>
      <path d="M103 26 L110 6 L117 26 Z" fill="#f3d9b1" stroke="#20243a" stroke-width="4" stroke-linejoin="round"/>
      <path d="M107.5 13 L110 6 L112.5 13 Z" fill="#20243a"/>
    </g>
    <path d="M110 40c52 0 88 34 88 92 0 58-38 92-88 92s-88-34-88-92c0-58 36-92 88-92z" fill="#ff6847" stroke="#20243a" stroke-width="5"/>
    <g clip-path="url(#pipBody)"><ellipse cx="110" cy="200" rx="64" ry="44" fill="#ffd2c2"/></g>
    <g class="pip-arm-l"><path d="M28 140 q-18 8 -14 26" fill="none" stroke="#20243a" stroke-width="6" stroke-linecap="round"/></g>
    <g class="pip-arm-r"><path d="M192 140 q18 8 14 26" fill="none" stroke="#20243a" stroke-width="6" stroke-linecap="round"/></g>
    <g class="pip-eyes">
      <g class="pip-eye" transform="translate(80 118)">
        <ellipse rx="19" ry="22" fill="#fff" stroke="#20243a" stroke-width="4"/>
        <g class="pip-pupil"><circle r="9" fill="#20243a"/><circle cx="3" cy="-4" r="3" fill="#fff"/></g>
        <rect class="pip-lid" x="-22" y="-26" width="44" height="0" fill="#ff6847"/>
      </g>
      <g class="pip-eye" transform="translate(140 118)">
        <ellipse rx="19" ry="22" fill="#fff" stroke="#20243a" stroke-width="4"/>
        <g class="pip-pupil"><circle r="9" fill="#20243a"/><circle cx="3" cy="-4" r="3" fill="#fff"/></g>
        <rect class="pip-lid" x="-22" y="-26" width="44" height="0" fill="#ff6847"/>
      </g>
    </g>
    <ellipse cx="58" cy="152" rx="11" ry="6" fill="#ff9a80"/>
    <ellipse cx="162" cy="152" rx="11" ry="6" fill="#ff9a80"/>
    <g transform="translate(110 160)">
      <path class="pip-smile" d="M-16 0 q16 14 32 0" fill="none" stroke="#20243a" stroke-width="5" stroke-linecap="round"/>
      <ellipse class="pip-mouth" rx="12" ry="0" fill="#20243a"/>
    </g>
  </g>
</svg>`;

const STYLE = `
.pip-host{display:inline-block;line-height:0}
.pip-host svg{width:100%;height:auto;overflow:visible}
.pip-all{transform-origin:110px 224px;animation:pip-breathe 3.2s ease-in-out infinite}
.pip-host.bounce .pip-all{animation:pip-bounce .55s cubic-bezier(.3,1.6,.5,1)}
.pip-host.wave .pip-arm-r{animation:pip-wave .5s ease-in-out 3;transform-origin:192px 140px}
.pip-host.think .pip-tuft{animation:pip-wiggle .9s ease-in-out infinite;transform-origin:110px 46px}
.pip-tuft{transform-origin:110px 46px}
.pip-pupil{transition:transform .18s ease-out}
@keyframes pip-breathe{0%,100%{transform:scale(1,1)}50%{transform:scale(1.015,.985)}}
@keyframes pip-bounce{0%{transform:translateY(0) scale(1)}30%{transform:translateY(-26px) scale(.96,1.05)}60%{transform:translateY(0) scale(1.06,.94)}100%{transform:none}}
@keyframes pip-wave{0%,100%{transform:rotate(0)}50%{transform:rotate(-38deg)}}
@keyframes pip-wiggle{0%,100%{transform:rotate(-8deg)}50%{transform:rotate(8deg)}}
@media (prefers-reduced-motion:reduce){.pip-all,.pip-tuft,.pip-arm-r{animation:none!important}}
`;

export function mountPip(host) {
  if (!document.getElementById("pip-style")) {
    const s = document.createElement("style"); s.id = "pip-style"; s.textContent = STYLE; document.head.append(s);
  }
  host.classList.add("pip-host");
  host.innerHTML = SVG;
  const mouth = host.querySelector(".pip-mouth"), smile = host.querySelector(".pip-smile");
  const pupils = [...host.querySelectorAll(".pip-pupil")], lids = [...host.querySelectorAll(".pip-lid")];
  let level = 0, target = 0, follow = true;

  function frame() {
    level += (target - level) * 0.35;
    const open = Math.max(0, Math.min(1, level));
    mouth.setAttribute("ry", (open * 13).toFixed(1));
    smile.style.opacity = open > 0.08 ? 0 : 1;
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  function blink() {
    lids.forEach(l => l.animate([{ height: "0px" }, { height: "52px" }, { height: "0px" }], { duration: 170 }));
    setTimeout(blink, 2200 + Math.random() * 3200);
  }
  setTimeout(blink, 1500);

  function look(dx, dy) { pupils.forEach(p => p.setAttribute("transform", `translate(${dx * 7} ${dy * 8})`)); }
  addEventListener("pointermove", (e) => {
    if (!follow) return;
    const r = host.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    const dx = e.clientX - cx, dy = e.clientY - cy, d = Math.hypot(dx, dy) || 1;
    look(dx / Math.max(d, 160), dy / Math.max(d, 160));
  });

  function once(cls, ms) { host.classList.remove(cls); void host.offsetWidth; host.classList.add(cls); setTimeout(() => host.classList.remove(cls), ms); }
  return {
    talk(v) { target = v; },
    bounce() { once("bounce", 600); },
    wave() { once("wave", 1600); },
    think(on) { host.classList.toggle("think", !!on); },
    look(dx, dy) { follow = false; look(dx, dy); setTimeout(() => follow = true, 1800); },
  };
}

// Plays an audio element through an analyser and moves Pip's mouth with it.
let ctx;
export function speakWith(pip, audio) {
  try {
    ctx = ctx || new AudioContext();
    if (ctx.state === "suspended") ctx.resume();
    if (!audio._pipNode) {
      audio._pipNode = ctx.createMediaElementSource(audio);
      audio._pipAnalyser = ctx.createAnalyser();
      audio._pipAnalyser.fftSize = 512;
      audio._pipNode.connect(audio._pipAnalyser).connect(ctx.destination);
    }
    const an = audio._pipAnalyser, buf = new Uint8Array(an.fftSize);
    const tick = () => {
      if (audio.paused || audio.ended) { pip.talk(0); return; }
      an.getByteTimeDomainData(buf);
      let sum = 0; for (const v of buf) sum += (v - 128) ** 2;
      pip.talk(Math.min(1, Math.sqrt(sum / buf.length) / 28));
      requestAnimationFrame(tick);
    };
    audio.addEventListener("play", tick);
  } catch (e) { /* Mouth stays still; audio still plays. */ }
}

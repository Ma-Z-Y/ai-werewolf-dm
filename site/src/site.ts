import { animate, inView, scroll, stagger } from "motion";

const root = document.documentElement;
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const header = document.querySelector<HTMLElement>("[data-header]");

if (!reduceMotion.matches) {
  root.classList.add("motion-ready");

  try {
    const heroElements = [
      ".hero-kicker",
      ".hero-title",
      ".hero-lead",
      ".hero-actions",
      ".hero-note",
    ]
      .map((selector) => document.querySelector(selector))
      .filter((element): element is Element => element !== null);

    animate(
      heroElements,
      { opacity: [0, 1], y: [22, 0] },
      {
        duration: 0.82,
        delay: stagger(0.08),
        ease: [0.22, 1, 0.36, 1],
      },
    );

    inView(
      ".reveal",
      (element) => {
        animate(
          element,
          { opacity: [0, 1], y: [28, 0] },
          {
            duration: 0.72,
            ease: [0.22, 1, 0.36, 1],
          },
        );
      },
      { margin: "0px 0px -12% 0px" },
    );

    scroll(
      animate(
        ".progress-bar",
        { scaleX: [0, 1] },
        { ease: "linear", duration: 1 },
      ),
    );
  } catch (error) {
    root.classList.remove("motion-ready");
    console.error("Motion setup failed; continuing with static content.", error);
  }
}

if (header) {
  const updateHeader = () => {
    header.dataset.scrolled = String(window.scrollY > 24);
  };

  updateHeader();
  window.addEventListener("scroll", updateHeader, { passive: true });
}

if (!reduceMotion.matches && window.matchMedia("(pointer: fine)").matches) {
  let pointerX = 62;
  let pointerY = 36;
  let frame = 0;

  window.addEventListener(
    "pointermove",
    (event) => {
      pointerX = (event.clientX / window.innerWidth) * 100;
      pointerY = (event.clientY / window.innerHeight) * 100;

      if (frame !== 0) return;
      frame = window.requestAnimationFrame(() => {
        root.style.setProperty("--pointer-x", `${pointerX.toFixed(2)}%`);
        root.style.setProperty("--pointer-y", `${pointerY.toFixed(2)}%`);
        frame = 0;
      });
    },
    { passive: true },
  );
}

const copyButton = document.querySelector<HTMLButtonElement>("[data-copy]");
const copyStatus = document.querySelector<HTMLElement>(".copy-status");

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const input = document.createElement("textarea");
    input.value = text;
    input.setAttribute("readonly", "");
    input.style.position = "fixed";
    input.style.opacity = "0";
    document.body.append(input);
    input.select();
    const copied = document.execCommand("copy");
    input.remove();
    return copied;
  }
}

function showCopyStatus(message: string) {
  if (!copyStatus) return;
  copyStatus.textContent = message;
  copyStatus.dataset.visible = "true";
  window.setTimeout(() => {
    copyStatus.dataset.visible = "false";
  }, 1800);
}

copyButton?.addEventListener("click", async () => {
  const text = copyButton.dataset.copy;
  if (!text) return;

  const copied = await copyText(text);
  copyButton.textContent = copied ? "已复制" : "复制失败";
  showCopyStatus(copied ? "部署命令已复制" : "请手动复制命令");
  window.setTimeout(() => {
    copyButton.textContent = "复制";
  }, 1800);
});

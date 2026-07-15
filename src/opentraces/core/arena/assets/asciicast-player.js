/* Vendored offline bench.v0 asciicast player. No network dependencies. */
(() => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  async function play(button) {
    const target = document.getElementById(button.dataset.target);
    const response = await fetch(button.dataset.cast);
    const lines = (await response.text()).trim().split("\n").slice(1);
    const seek = Math.max(0, Number(button.dataset.seekOffsetMs || 0) / 1000);
    target.textContent = "";
    let prior = seek;
    for (const line of lines) {
      const event = JSON.parse(line);
      if (event[0] < seek) {
        target.textContent += event[2];
        continue;
      }
      await sleep(Math.max(0, (event[0] - prior) * 1000));
      target.textContent += event[2];
      prior = event[0];
    }
  }
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-cast]");
    if (button) play(button).catch((error) => { button.title = String(error); });
  });
})();

// A4 自動分頁:把 <main id="src"> 裡的區塊依序排進 A4 頁面,放不下就換頁。
// - h2 會跟下一個區塊綁在一起,不會標題留在頁底
// - 單一區塊比一整頁還高時,依子元素拆到下一頁
// - .diagram-page 固定單獨一頁;依流程圖寬高比選直向/橫向,再等比縮放填滿該頁
// mermaid 設定必須在任何 await 之前同步執行,否則 mermaid 會先用預設主題自動畫圖
if (window.mermaid) {
  mermaid.initialize({
    startOnLoad: false,
    layout: "elk",
    look: "classic",
    theme: "base",
    themeVariables: {
      fontFamily: '"Iansui", "Microsoft JhengHei", "PingFang TC", sans-serif',
      fontSize: "15px",
      primaryColor: "#d6e4ff",
      primaryBorderColor: "#1d3f8f",
      primaryTextColor: "#0b1220",
      lineColor: "#1f2937",
      edgeLabelBackground: "#ffffff",
      tertiaryColor: "#ffffff"
    },
    htmlLabels: true,
    markdownAutoWrap: false,
    flowchart: { curve: "linear", htmlLabels: true, useMaxWidth: false, wrappingWidth: 400, nodeSpacing: 22, rankSpacing: 30, padding: 10 }
  });
}

(async function () {
  const src = document.getElementById("src");
  if (!src) return;

  // 先確定芫荽字型載入完成,流程圖量字寬才準
  if (document.fonts) {
    try { await document.fonts.load('15px "Iansui"', "芫荽"); } catch (e) {}
    await document.fonts.ready;
  }

  if (window.mermaid) {
    try {
      await mermaid.run({ querySelector: "#src .mermaid" });
    } catch (e) {
      console.error("mermaid 繪圖失敗", e);
    }
  }

  const pages = document.createElement("div");
  pages.id = "pages";
  document.body.appendChild(pages);

  let body = null;
  function newPage(landscape) {
    const page = document.createElement("div");
    page.className = "page" + (landscape ? " landscape" : "");
    body = document.createElement("div");
    body.className = "page-body";
    page.appendChild(body);
    pages.appendChild(page);
    return body;
  }
  const overflowing = () => body.scrollHeight > body.clientHeight + 1;

  // 單一區塊太高:把子元素一個個放回去,滿了就在新頁接續(外框複製一份)
  function split(el) {
    const kids = Array.from(el.children);
    if (kids.length < 2) return false;
    kids.forEach(k => k.remove());
    let shell = el;
    for (const k of kids) {
      shell.appendChild(k);
      if (overflowing() && shell.children.length > 1) {
        k.remove();
        newPage(false);
        shell = el.cloneNode(false);
        shell.removeAttribute("id");
        body.appendChild(shell);
        shell.appendChild(k);
      }
    }
    return true;
  }

  function placeDiagram(block) {
    const svg = block.querySelector("svg");
    if (!svg) { newPage(false); body.appendChild(block); body = null; return; }
    const vb = svg.viewBox.baseVal;
    const w = vb && vb.width ? vb.width : svg.getBBox().width;
    const h = vb && vb.height ? vb.height : svg.getBBox().height;
    const landscape = w > h;
    svg.style.maxWidth = "none";

    // 先試 A4;縮放後字太小(< MIN_SCALE)就改用 A3。MIN_SCALE 是全專案唯一的門檻定義
    const MIN_SCALE = 0.7;
    const fit = () => {
      svg.setAttribute("width", 0);
      svg.setAttribute("height", 0);
      const availW = body.clientWidth;
      const availH = body.clientHeight - block.offsetHeight - 4;
      return Math.min(availW / w, availH / h, 1.4);
    };
    newPage(landscape);
    body.appendChild(block);
    let s = fit();
    let size = "A4";
    if (s < MIN_SCALE) {
      body.parentElement.classList.add("a3");
      s = fit();
      size = "A3";
    }
    svg.setAttribute("width", Math.floor(w * s));
    svg.setAttribute("height", Math.floor(h * s));
    block.dataset.orient = landscape ? "landscape" : "portrait";
    block.dataset.size = size;
    block.dataset.scale = s.toFixed(2);
    block.dataset.minScale = MIN_SCALE;  // verify.py 從這裡讀門檻,門檻只定義在這一處
    body = null;
  }

  // h2 與下一個區塊綁成一組
  const blocks = Array.from(src.children);
  const groups = [];
  for (let i = 0; i < blocks.length; i++) {
    const el = blocks[i];
    const next = blocks[i + 1];
    if (el.tagName === "H2" && next && !next.classList.contains("diagram-page")) {
      groups.push([el, next]);
      i++;
    } else {
      groups.push([el]);
    }
  }

  for (const g of groups) {
    if (g[0].classList.contains("diagram-page")) { placeDiagram(g[0]); continue; }
    if (!body) newPage(false);
    const hadContent = body.children.length > 0;
    g.forEach(el => body.appendChild(el));
    if (overflowing() && hadContent) {
      newPage(false);
      g.forEach(el => body.appendChild(el));
    }
    if (overflowing()) split(g[g.length - 1]);
    if (overflowing()) {
      // 仍放不下(單一子元素就超過一頁):讓這頁長高,不裁掉內容
      body.parentElement.style.height = "auto";
      body.style.overflow = "visible";
      console.warn("區塊超過一頁 A4,已改為延伸頁面", g);
    }
  }

  src.remove();
  const all = pages.querySelectorAll(".page");
  all.forEach((p, i) => {
    const no = document.createElement("div");
    no.className = "page-no";
    no.textContent = (i + 1) + " / " + all.length;
    p.appendChild(no);
  });
  document.body.dataset.pages = all.length;
  // ?view=diagram:只顯示流程圖那頁(給 _tools/verify.py 截圖用)
  if (new URLSearchParams(location.search).get("view") === "diagram") document.body.classList.add("view-diagram");
})();

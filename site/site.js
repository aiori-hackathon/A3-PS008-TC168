const menu=document.querySelector("#menu-toggle");
const nav=document.querySelector("#main-nav");
menu.addEventListener("click",()=>{const open=menu.getAttribute("aria-expanded")!=="true";menu.setAttribute("aria-expanded",String(open));nav.classList.toggle("open",open)});
nav.querySelectorAll("a").forEach(link=>link.addEventListener("click",()=>{menu.setAttribute("aria-expanded","false");nav.classList.remove("open")}));
document.querySelector("#copy-commands").addEventListener("click",async event=>{const button=event.currentTarget;try{await navigator.clipboard.writeText(document.querySelector("#install-commands").textContent);button.textContent="Copied";setTimeout(()=>button.textContent="Copy commands",1600)}catch{button.textContent="Select and copy"}});

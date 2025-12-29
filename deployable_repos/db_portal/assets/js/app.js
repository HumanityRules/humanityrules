//
// Import deps with the dep name or local files with a relative path, for example:
//
//     import {Socket} from "phoenix"
//     import socket from "./socket"
//
import "phoenix_html"
import {Socket} from "phoenix"
import topbar from "../vendor/topbar"
import {LiveSocket} from "phoenix_live_view"

import { CodeEditorHook } from "../../deps/live_monaco_editor/priv/static/live_monaco_editor.esm"


let execJS = (selector, attr) => {
    console.log("inside execJS")
  document.querySelectorAll(selector).forEach(el => liveSocket.execJS(el, el.getAttribute(attr)))
    console.log("exiting execJS")
}

let hooks = {

  // Copy from https://fly.io/phoenix-files/copy-to-clipboard-with-phoenix-liveview/
  // but added the innerHTML fallback to handle <span>s, for example
  Copy: {
    mounted() {
      let { to } = this.el.dataset; // gets this.el.dataset.to
      this.el.addEventListener("click", (ev) => {
        ev.preventDefault();
        let text = document.querySelector(to).value || document.querySelector(to).innerHTML
        navigator.clipboard.writeText(text)
      });
    },
  },

  chartHook: {
    mounted() {
        console.log("Mounted");
    },
    updated() {
        console.log("updated");
        let data = JSON.parse(this.el.dataset.cdata);
        let chart_id = this.el.dataset.chart_id;
    }
  },
    // Accessible focus wrapping
  FocusWrap: {
    mounted(){
        this.content = document.querySelector(this.el.getAttribute("data-content"))
        this.focusStart = this.el.querySelector(`#${this.el.id}-start`)
        this.focusEnd = this.el.querySelector(`#${this.el.id}-end`)
        this.focusStart.addEventListener("focus", () => Focus.focusLastDescendant(this.content))
        this.focusEnd.addEventListener("focus", () => Focus.focusFirstDescendant(this.content))
        this.content.addEventListener("phx:show-end", () => this.content.focus())
        if(window.getComputedStyle(this.content).display !== "none"){
        Focus.focusFirstDescendant(this.content)
        }
    },
  },
  tooltip : {

    initTooltip() {
      let hook = this;
      this.el.addEventListener("mouseenter", e => {
        console.log(e);
        let id = e.srcElement.id;
        hook.pushEvent("tooltip", {idx: id});
      });
    },

    mounted() {
      this.initTooltip();
    },
  },
  CodeEditorHook : CodeEditorHook
};
// Accessible focus handling
let Focus = {
  focusMain(){
    let target = document.querySelector("main h1") || document.querySelector("main")
    if(target){
      let origTabIndex = target.tabIndex
      target.tabIndex = -1
      target.focus()
      target.tabIndex = origTabIndex
    }
  },
  // Subject to the W3C Software License at https://www.w3.org/Consortium/Legal/2015/copyright-software-and-document
  isFocusable(el){
    if(el.tabIndex > 0 || (el.tabIndex === 0 && el.getAttribute("tabIndex") !== null)){ return true }
    if(el.disabled){ return false }

    switch(el.nodeName) {
      case "A":
        return !!el.href && el.rel !== "ignore"
      case "INPUT":
        return el.type != "hidden" && el.type !== "file"
      case "BUTTON":
      case "SELECT":
      case "TEXTAREA":
        return true
      default:
        return false
    }
  },
  // Subject to the W3C Software License at https://www.w3.org/Consortium/Legal/2015/copyright-software-and-document
  attemptFocus(el){
    if(!el){ return }
    if(!this.isFocusable(el)){ return false }
    try {
      el.focus()
    } catch(e){}

    return document.activeElement === el
  },
  // Subject to the W3C Software License at https://www.w3.org/Consortium/Legal/2015/copyright-software-and-document
  focusFirstDescendant(el){
    for(let i = 0; i < el.childNodes.length; i++){
      let child = el.childNodes[i]
      if(this.attemptFocus(child) || this.focusFirstDescendant(child)){
        return true
      }
    }
    return false
  },
  // Subject to the W3C Software License at https://www.w3.org/Consortium/Legal/2015/copyright-software-and-document
  focusLastDescendant(element){
    for(let i = element.childNodes.length - 1; i >= 0; i--){
      let child = element.childNodes[i]
      if(this.attemptFocus(child) || this.focusLastDescendant(child)){
        return true
      }
    }
    return false
  },
}


let csrfToken = document.querySelector("meta[name='csrf-token']").getAttribute("content")
let liveSocket = new LiveSocket('/live', Socket, {
  dom: {
    onBeforeElUpdated(from, to) {
      if (from.__x) {
        window.Alpine.clone(from.__x, to)
      }
    }
  },
  params: {
    _csrf_token: csrfToken,
    // from https://elixirforum.com/t/display-datetime-in-users-timezone/45732/9?u=rliebling
    locale: Intl.NumberFormat().resolvedOptions().locale,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    // USE POLYFILL FOR IE11
    // timezone: moment.tz.guess(),
    timezone_offset: -(new Date().getTimezoneOffset() / 60),
  },
  hooks: hooks
})

// Show progress bar on live navigation and form submits
topbar.config({barColors: {0: "rgba(147, 51, 234, 1)"}, shadowColor: "rgba(0, 0, 0, .3)"})
window.addEventListener("phx:page-loading-start", info => topbar.show())
window.addEventListener("phx:page-loading-stop", info => topbar.hide())

window.addEventListener("js:exec", e => e.target[e.detail.call](...e.detail.args))
window.addEventListener("app:log", e => console.log("log:", e.detail))

// connect if there are any LiveViews on the page
liveSocket.connect()

// expose liveSocket on window for web console debug logs and latency simulation:
// >> liveSocket.enableDebug()
// >> liveSocket.enableLatencySim(1000)
window.liveSocket = liveSocket
window.execJS = execJS

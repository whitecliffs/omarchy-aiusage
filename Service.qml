import QtQuick
import QtQml
import Quickshell
import Quickshell.Io

// Runs fetch.py once per tracked provider (in parallel) and holds the
// normalized results. fetch.py always exits 0 with JSON, so a failed fetch is
// state to render, not an error to swallow.
Item {
  id: root

  property var settings: ({})
  property var enabled: []          // provider ids, in tab order

  property var results: ({})        // id -> normalized result object
  property var busy: ({})           // id -> bool
  property var detected: ({})       // id -> bool (credentials present)
  property double lastFetch: 0

  readonly property string helperPath: String(Qt.resolvedUrl("fetch.py")).replace(/^file:\/\//, "")
  readonly property string enabledKey: enabled.join(",")
  readonly property bool anyBusy: {
    for (var k in busy) if (busy[k]) return true
    return false
  }

  readonly property int refreshIntervalSec: {
    var v = parseInt(setting("refreshIntervalSec", 120))
    return isFinite(v) ? Math.max(30, Math.min(3600, v)) : 120
  }

  function setting(name, fallback) {
    var v = settings ? settings[name] : undefined
    return v === undefined || v === null ? fallback : v
  }

  function optsJson(force) {
    return JSON.stringify({
      forceRefresh: force === true,
      lowBalance: setting("lowBalance", 5),
      keyCommand: String(setting("keyCommand", ""))
    })
  }

  function copyWith(map, key, value) {
    var out = {}
    for (var k in map) out[k] = map[k]
    out[key] = value
    return out
  }

  function procFor(id) {
    for (var i = 0; i < pool.count; i++) {
      var p = pool.objectAt(i)
      if (p && p.provider === id) return p
    }
    return null
  }

  function refreshOne(id, force) {
    var p = procFor(id)
    if (!p || p.running || helperPath === "") return
    busy = copyWith(busy, id, true)
    p.command = ["python3", "-I", helperPath, id, optsJson(force)]
    p.running = true
  }

  function refreshAll(force) {
    for (var i = 0; i < enabled.length; i++) refreshOne(enabled[i], force)
  }

  function detect() {
    if (detectProc.running) return
    detectProc.command = ["python3", "-I", helperPath, "detect"]
    detectProc.running = true
  }

  function apply(id, raw) {
    var parsed = null
    try { parsed = JSON.parse(String(raw || "")) } catch (e) { parsed = null }
    if (!parsed || typeof parsed !== "object")
      parsed = { ok: false, error: "Could not read the helper output", hint: "", windows: [], rows: [], sections: [], pct: -1 }
    results = copyWith(results, id, parsed)
    busy = copyWith(busy, id, false)
    lastFetch = Date.now()
  }

  onEnabledKeyChanged: refreshAll()

  Timer {
    interval: root.refreshIntervalSec * 1000
    repeat: true
    running: true
    triggeredOnStart: true
    onTriggered: { root.refreshAll(); root.detect() }
  }

  Instantiator {
    id: pool
    model: ["claude", "openai", "opencode", "antigravity", "conductor", "openrouter"]
    delegate: Process {
      id: proc
      required property string modelData
      property string provider: modelData
      property string capturedOut: ""
      running: false
      command: []
      stdout: StdioCollector {
        id: out
        waitForEnd: true
        onStreamFinished: {
          proc.capturedOut = text
          root.apply(proc.provider, text)
        }
      }
      onExited: function(code) {
        var raw = proc.capturedOut || out.text || ""
        if (raw) {
          root.apply(proc.provider, raw)
        } else if (code !== 0) {
          root.apply(proc.provider, JSON.stringify({
            ok: false,
            error: "Process exited with code " + code,
            hint: "", windows: [], rows: [], sections: [], pct: -1
          }))
        }
        proc.capturedOut = ""
      }
    }
  }

  Process {
    id: detectProc
    running: false
    command: []
    stdout: StdioCollector {
      id: detectOut
      waitForEnd: true
      onStreamFinished: {
        try { root.detected = JSON.parse(String(text || "")) } catch (e) {}
      }
    }
    onExited: function(code) {
      var raw = detectOut.text || ""
      if (raw) {
        try { root.detected = JSON.parse(String(raw)) } catch (e) {}
      }
    }
  }
}

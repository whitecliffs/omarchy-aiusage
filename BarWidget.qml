import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// AI Usage: live subscription usage in the bar, a tab per tracked provider in
// the popup, and a SETUP tab that chooses which providers are tracked.
//
// Bar — left click: popup · right click: refresh · middle click: SETUP
// Popup — h/l or ←/→: tab · j/k or ↑/↓: scroll · 1-9: jump to tab · r: refresh · Esc: close
//
// All colours and fonts come from the bar / Commons, so the widget follows
// whatever Omarchy theme is active with no colours of its own.
Panel {
  id: root
  moduleName: "jim.aiusage"
  ipcTarget: "jim.aiusage"
  manageIpc: false

  readonly property bool vertical: bar ? bar.vertical : false
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property color track: Util.alpha(foreground, 0.16)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  readonly property string glyph: ""          // nf-fa-bolt
  readonly property string glyphRefresh: ""   // nf-fa-refresh
  readonly property string glyphMinus: ""     // nf-fa-minus
  readonly property string glyphPlus: ""      // nf-fa-plus

  // ---- provider catalogue -------------------------------------------------
  readonly property var catalogue: [
    { id: "claude", name: "Claude", tag: "CL", blurb: "Claude Pro/Max - 5h session and weekly limits", url: "https://claude.ai/settings/usage" },
    { id: "openai", name: "OpenAI", tag: "OA", blurb: "ChatGPT / Codex - 5h and weekly limits", url: "https://chatgpt.com/codex/settings/usage" },
    { id: "opencode", name: "OpenCode", tag: "OC", blurb: "Go subscription limits across all machines", url: "https://opencode.ai/zen" },
    { id: "antigravity", name: "Antigravity", tag: "AG", blurb: "Google Antigravity - Gemini and Claude/GPT quotas", url: "https://antigravity.google" },
    { id: "conductor", name: "Conductor", tag: "CO", blurb: "AI Junction credit balance from Conductor", url: "" },
    { id: "openrouter", name: "OpenRouter", tag: "OR", blurb: "Credit balance and key spend", url: "https://openrouter.ai/credits" }
  ]

  function meta(id) {
    for (var i = 0; i < catalogue.length; i++) if (catalogue[i].id === id) return catalogue[i]
    return null
  }

  // Tracked providers, validated and de-duplicated, in the user's order.
  readonly property var tracked: {
    var raw = setting("providers", [])
    var out = []
    if (!raw || !raw.length) return out
    for (var i = 0; i < raw.length; i++) {
      var id = String(raw[i])
      if (meta(id) && out.indexOf(id) === -1) out.push(id)
    }
    return out
  }

  readonly property var tabs: {
    var t = tracked.slice()
    t.push("setup")
    return t
  }
  property string tab: ""
  readonly property string activeTab: tabs.indexOf(tab) !== -1 ? tab : tabs[0]

  readonly property real warnPct: {
    var v = parseFloat(setting("warnPct", 90))
    return isFinite(v) ? v : 90
  }

  function result(id) { return service.results[id] || null }
  function pctOf(id) { var r = result(id); return r && r.ok && r.pct >= 0 ? r.pct : -1 }

  readonly property real worstPct: {
    var w = -1
    for (var i = 0; i < tracked.length; i++) w = Math.max(w, pctOf(tracked[i]))
    return w
  }
  readonly property bool barAlarming: worstPct >= warnPct
  readonly property string labelMode: String(setting("barLabel", "All"))
  readonly property bool iconOnly: vertical || labelMode === "Icon only" || tracked.length === 0

  readonly property bool showRemaining: String(setting("usageDisplay", "Used")) === "Remaining"
  property int cycleIndex: 0
  onLabelModeChanged: cycleIndex = 0
  onTrackedChanged: cycleIndex = 0

  Timer {
    interval: 5000
    repeat: true
    running: root.labelMode === "Cycle" && !root.opened && !root.iconOnly && root.tracked.length > 1
    onTriggered: root.cycleIndex = (root.cycleIndex + 1) % root.tracked.length
  }

  function displayPct(used) {
    return showRemaining ? Math.max(0, 100 - used) : used
  }

  function headlineFor(r) {
    if (!r || !r.ok) return ""
    if (r.pct >= 0 && /%$/.test(String(r.headline || "")))
      return Math.round(displayPct(r.pct)) + "%"
    return r.headline || "—"
  }

  function segmentFor(id) {
    var m = meta(id)
    var r = result(id)
    var text = !r ? "…" : (!r.ok ? "!" : headlineFor(r))
    return m.tag + " " + text
  }

  property rect popupAnchorRect: Qt.rect(0, 0, 0, 0)
  property string frozenBarText: ""
  readonly property string barText: {
    if (opened && frozenBarText !== "") return frozenBarText
    if (iconOnly) return glyph
    if (labelMode === "Cycle")
      return glyph + "  " + segmentFor(tracked[cycleIndex % tracked.length])
    if (labelMode === "Worst") {
      var best = "", bp = -2
      for (var i = 0; i < tracked.length; i++) {
        var p = pctOf(tracked[i])
        if (p > bp) { bp = p; best = tracked[i] }
      }
      return glyph + "  " + (best === "" ? "—" : segmentFor(best))
    }
    var parts = []
    for (var j = 0; j < tracked.length; j++) parts.push(segmentFor(tracked[j]))
    return glyph + "  " + parts.join("   ")
  }

  readonly property string tooltip: {
    if (tracked.length === 0) return "AI Usage - click to choose subscriptions"
    var lines = []
    for (var i = 0; i < tracked.length; i++) {
      var m = meta(tracked[i]), r = result(tracked[i])
      lines.push(m.name + ": " + (!r ? "loading" : (r.ok ? (headlineFor(r) + (r.pct >= 0 && /%$/.test(r.headline || "") ? (showRemaining ? " remaining" : " used") : "")) : r.error)))
    }
    return lines.join("\n")
  }

  // ---- helpers ------------------------------------------------------------
  function relReset(epoch, nowMs) {
    if (!epoch || epoch <= 0) return ""
    var s = Math.round(epoch - nowMs / 1000)
    if (s <= 0) return "resetting now"
    var d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60)
    if (d > 0) return "resets in " + d + "d " + h + "h"
    if (h > 0) return "resets in " + h + "h " + m + "m"
    return "resets in " + Math.max(1, m) + "m"
  }

  function relAge(epoch, nowMs) {
    if (!epoch) return ""
    var s = Math.max(0, Math.round(nowMs / 1000 - epoch))
    if (s < 45) return "just now"
    if (s < 3600) return Math.round(s / 60) + "m ago"
    return Math.round(s / 3600) + "h ago"
  }

  function persistPluginSetting(name, value) {
    if (!root.bar || !root.bar.shell || typeof root.bar.shell.updateEntryInline !== "function") return
    var entry = { id: root.moduleName }
    for (var key in settings) if (key !== "id" && key !== name) entry[key] = settings[key]
    entry[name] = value
    root.bar.shell.updateEntryInline(root.moduleName, entry)
  }

  function isTracked(id) { return tracked.indexOf(id) !== -1 }

  function toggleProvider(id) {
    var next = tracked.slice()
    var at = next.indexOf(id)
    if (at === -1) next.push(id)
    else next.splice(at, 1)
    persistPluginSetting("providers", next)
  }

  function setInterval(delta) {
    var steps = [30, 60, 120, 300, 600, 900, 1800, 3600]
    var cur = service.refreshIntervalSec, at = 0
    for (var i = 0; i < steps.length; i++) if (steps[i] <= cur) at = i
    at = Math.max(0, Math.min(steps.length - 1, at + delta))
    persistPluginSetting("refreshIntervalSec", steps[at])
  }

  function fmtInterval(s) { return s < 120 ? s + "s" : Math.round(s / 60) + " min" }

  function cycleLabel() {
    var modes = ["All", "Worst", "Cycle", "Icon only"]
    persistPluginSetting("barLabel", modes[(modes.indexOf(labelMode) + 1) % modes.length])
  }

  function refresh(force) { service.refreshAll(force !== false); service.detect() }

  function openTab(name) {
    if (tabs.indexOf(name) === -1) return
    tab = name
    if (panelFlick) panelFlick.contentY = 0
  }

  function stepTab(delta) {
    var at = tabs.indexOf(activeTab)
    openTab(tabs[(at + delta + tabs.length) % tabs.length])
  }

  function scrollBy(dy) {
    var max = Math.max(0, panelFlick.contentHeight - panelFlick.height)
    panelFlick.contentY = Math.max(0, Math.min(max, panelFlick.contentY + dy * Style.space(48)))
  }

  function pressed(button) {
    if (button === Qt.RightButton) refresh()
    else if (button === Qt.MiddleButton) { open(); openTab("setup") }
    else toggle()
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight
  readonly property real openPanelIndicatorWidth: button.labelWidth

  onOpenedChanged: {
    if (!opened) {
      frozenBarText = ""
      return
    }
    var pos = button.mapToItem(popupAnchor.parent, 0, 0)
    popupAnchorRect = Qt.rect(pos.x, pos.y, button.width, button.height)
    frozenBarText = barText
    // First run (nothing tracked yet) lands on SETUP; otherwise the first provider.
    tab = tracked.length === 0 ? "setup" : tracked[0]
    if (panelFlick) panelFlick.contentY = 0
    root.refresh(false)
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  Service {
    id: service
    settings: root.settings
    enabled: root.tracked
  }

  SystemClock {
    id: tick
    precision: SystemClock.Seconds
    enabled: root.opened
  }

  IpcHandler {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { root.refresh(); return "ok" }
    function status(): string {
      var out = []
      for (var i = 0; i < root.tracked.length; i++) {
        var m = root.meta(root.tracked[i]), r = root.result(root.tracked[i])
        out.push(m.name + "=" + (!r ? "loading" : (r.ok ? r.headline : "error")))
      }
      return out.join(" ")
    }
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.barText
    fontSize: root.iconOnly ? Style.bar.iconFont : Style.font.body
    active: root.barAlarming
    horizontalMargin: 8.5
    tooltipText: root.tooltip
    onPressed: function(code) { root.pressed(code) }
  }

  // Keep the popup anchored to the button's position at opening, even if
  // neighboring bar widgets change size while the popup is visible.
  Item {
    id: popupAnchor
    parent: button.QsWindow.window ? button.QsWindow.window.contentItem : button
    x: root.popupAnchorRect.x
    y: root.popupAnchorRect.y
    width: root.popupAnchorRect.width
    height: root.popupAnchorRect.height
  }

  KeyboardPanel {
    id: panel
    anchorItem: popupAnchor
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Math.max(Style.space(470), tabStrip.implicitWidth + panel.padding * 2 + Style.space(8)))
    contentHeight: panel.fittedContentHeight(tabStrip.implicitHeight + Style.space(12) + body.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) {
        if (dx !== 0) root.stepTab(dx)
        if (dy !== 0) root.scrollBy(dy)
      }
      onActivateRequested: root.refresh()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        var k = String(t).toLowerCase()
        if (k === "r") root.refresh()
        else if (k === "h") root.stepTab(-1)
        else if (k === "l") root.stepTab(1)
        else if (k >= "1" && k <= "9") root.openTab(root.tabs[parseInt(k) - 1] || "")
      }

      ColumnLayout {
        anchors.fill: parent
        spacing: Style.space(12)

        // ---------- tab strip ----------
        Row {
          id: tabStrip
          Layout.fillWidth: true
          spacing: Style.space(14)

          Repeater {
            model: root.tabs

            Item {
              id: tabItem
              required property string modelData
              readonly property bool current: root.activeTab === modelData
              readonly property string title: modelData === "setup" ? "Setup" : root.meta(modelData).name
              readonly property real p: modelData === "setup" ? -1 : root.pctOf(modelData)
              implicitWidth: tabRow.implicitWidth
              implicitHeight: tabRow.implicitHeight + Style.space(6)

              Row {
                id: tabRow
                spacing: Style.space(5)
                Text {
                  text: tabItem.title
                  textFormat: Text.PlainText
                  color: tabItem.current ? root.foreground : root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: tabItem.current
                }
                // status dot: urgent when that provider is near its limit
                Rectangle {
                  visible: tabItem.p >= root.warnPct
                  width: Style.space(6); height: width; radius: width / 2
                  color: root.urgent
                  anchors.verticalCenter: parent.verticalCenter
                }
              }

              Rectangle {
                visible: tabItem.current
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                height: Math.max(1, Style.space(2))
                radius: height / 2
                color: root.foreground
              }

              MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: root.openTab(tabItem.modelData)
              }
            }
          }
        }

        // ---------- body ----------
        Flickable {
          id: panelFlick
          Layout.fillWidth: true
          Layout.fillHeight: true
          contentWidth: width
          contentHeight: body.implicitHeight
          clip: true
          boundsBehavior: Flickable.StopAtBounds
          flickableDirection: Flickable.VerticalFlick
          interactive: contentHeight > height
          ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

          Column {
            id: body
            width: panelFlick.width
            spacing: Style.space(12)

            Loader {
              width: parent.width
              active: root.activeTab !== "setup" && root.activeTab !== undefined
              visible: active
              sourceComponent: providerView
            }

            Loader {
              width: parent.width
              active: root.activeTab === "setup"
              visible: active
              sourceComponent: setupView
            }
          }
        }
      }
    }
  }

  // ====================================================================
  // Provider tab
  // ====================================================================
  Component {
    id: providerView

    Column {
      id: pv
      readonly property string pid: root.activeTab
      readonly property var info: root.meta(pid)
      readonly property var res: service.results[pid] || null
      readonly property bool loading: res === null
      spacing: Style.space(12)

      PanelHero {
        id: hero
        width: parent.width
        title: pv.info ? pv.info.name : ""
        meta: pv.res && pv.res.plan ? pv.res.plan : ""
        detail: pv.res && pv.res.ok ? root.headlineFor(pv.res) + (pv.res.pct >= 0 && /%$/.test(pv.res.headline || "") ? (root.showRemaining ? " remaining" : " used") : "") : ""
        foreground: root.foreground
        fontFamily: root.fontFamily

        iconComponent: Component {
          Text {
            text: pv.info ? pv.info.tag : ""
            textFormat: Text.PlainText
            color: pv.res && pv.res.ok && pv.res.pct >= root.warnPct ? root.urgent : root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.display
            font.bold: true
          }
        }

        trailingControl: Component {
          PanelActionButton {
            id: refreshButton
            iconText: root.glyphRefresh
            tooltipText: "Refresh"
            foreground: hero.foreground
            fontFamily: hero.fontFamily
            enabled: !service.busy[pv.pid]
            onClicked: service.refreshOne(pv.pid, true)

            RotationAnimation on rotation {
              running: service.busy[pv.pid] === true
              loops: Animation.Infinite
              from: 0
              to: 360
              duration: 900
              onRunningChanged: if (!running) refreshButton.rotation = 0
            }
          }
        }
      }

      Text {
        visible: pv.loading
        width: parent.width
        text: "Loading…"
        textFormat: Text.PlainText
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
      }

      Text {
        visible: pv.res !== null && !pv.res.ok
        width: parent.width
        text: pv.res ? pv.res.error : ""
        textFormat: Text.PlainText
        color: root.urgent
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        wrapMode: Text.WordWrap
      }

      Text {
        visible: pv.res !== null && String(pv.res.hint || "") !== ""
        width: parent.width
        text: pv.res ? String(pv.res.hint || "") : ""
        textFormat: Text.PlainText
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        wrapMode: Text.WordWrap
      }

      PanelSeparator {
        visible: pv.res !== null && pv.res.ok && (
          (pv.res.windows && pv.res.windows.length > 0) ||
          (pv.res.rows && pv.res.rows.length > 0) ||
          (pv.res.sections && pv.res.sections.length > 0)
        )
        foreground: root.foreground
      }

      Column {
        width: parent.width
        spacing: Style.space(12)
        visible: pv.res !== null && pv.res.ok

        Repeater {
          model: pv.res && pv.res.ok ? pv.res.windows : []
          WindowRow {
            required property var modelData
            width: parent.width
            win: modelData
          }
        }

        Column {
          width: parent.width
          spacing: Style.space(6)
          visible: pv.res !== null && pv.res.ok && pv.res.rows.length > 0

          Repeater {
            model: pv.res && pv.res.ok ? pv.res.rows : []
            LedgerRow {
              required property var modelData
              width: parent.width
              label: modelData.label
              value: modelData.value
            }
          }
        }

        Repeater {
          model: pv.res && pv.res.ok ? pv.res.sections : []

          Column {
            id: sectionCol
            required property var modelData
            width: parent.width
            spacing: Style.space(10)

            PanelSectionHeader {
              width: parent.width
              text: sectionCol.modelData.title || ""
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Repeater {
              model: sectionCol.modelData.windows || []
              WindowRow {
                required property var modelData
                width: parent.width
                win: modelData
              }
            }

            Repeater {
              model: sectionCol.modelData.rows || []
              LedgerRow {
                required property var modelData
                width: parent.width
                label: modelData.label || ""
                value: modelData.value || ""
              }
            }
          }
        }
      }

      Text {
        visible: text !== ""
        width: parent.width
        text: {
          if (!pv.res) return ""
          var age = root.relAge(pv.res.fetchedAt, tick.date.getTime())
          return (pv.res.stale ? "Last reading " : "Updated ") + age
        }
        textFormat: Text.PlainText
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        horizontalAlignment: Text.AlignHCenter
      }
    }
  }

  // ====================================================================
  // Setup tab
  // ====================================================================
  Component {
    id: setupView

    Column {
      spacing: Style.space(12)

      PanelHero {
        width: parent.width
        title: "AI Usage setup"
        meta: root.tracked.length === 0 ? "Choose what to track" : root.tracked.length + " tracked"
        foreground: root.foreground
        fontFamily: root.fontFamily
        iconComponent: Component {
          Text {
            text: root.glyph
            textFormat: Text.PlainText
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.display
          }
        }
      }

      PanelSectionHeader {
        width: parent.width
        text: "SUBSCRIPTIONS"
        foreground: root.foreground
        fontFamily: root.fontFamily
      }

      Repeater {
        model: root.catalogue

        Item {
          id: setupRow
          required property var modelData
          readonly property bool on: root.isTracked(modelData.id)
          readonly property bool found: service.detected[modelData.id] === true
          width: parent.width
          implicitHeight: Math.max(rowText.implicitHeight, toggle.implicitHeight) + Style.space(6)

          MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: root.toggleProvider(setupRow.modelData.id)
          }

          Column {
            id: rowText
            anchors.left: parent.left
            anchors.right: toggle.left
            anchors.rightMargin: Style.space(10)
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(1)

            Text {
              width: parent.width
              text: setupRow.modelData.name
              textFormat: Text.PlainText
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              elide: Text.ElideRight
            }
            Text {
              width: parent.width
              text: setupRow.modelData.blurb + (setupRow.found ? "" : "  ·  no login found")
              textFormat: Text.PlainText
              color: setupRow.found ? root.dim : root.urgent
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              elide: Text.ElideRight
            }
          }

          ToggleSwitch {
            id: toggle
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            checked: setupRow.on
            onToggled: root.toggleProvider(setupRow.modelData.id)
          }
        }
      }

      PanelSeparator { foreground: root.foreground }

      PanelSectionHeader {
        width: parent.width
        text: "DISPLAY"
        foreground: root.foreground
        fontFamily: root.fontFamily
      }

      RowLayout {
        width: parent.width
        spacing: Style.space(8)
        Text {
          Layout.fillWidth: true
          text: "Refresh every"
          textFormat: Text.PlainText
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
        }
        PanelActionButton {
          iconText: root.glyphMinus
          tooltipText: "Refresh less often"
          foreground: root.foreground
          fontFamily: root.fontFamily
          onClicked: root.setInterval(-1)
        }
        Text {
          Layout.preferredWidth: Style.space(56)
          horizontalAlignment: Text.AlignHCenter
          text: root.fmtInterval(service.refreshIntervalSec)
          textFormat: Text.PlainText
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
        }
        PanelActionButton {
          iconText: root.glyphPlus
          tooltipText: "Refresh more often"
          foreground: root.foreground
          fontFamily: root.fontFamily
          onClicked: root.setInterval(1)
        }
      }

      RowLayout {
        width: parent.width
        spacing: Style.space(8)
        Text {
          Layout.fillWidth: true
          text: "Show remaining"
          textFormat: Text.PlainText
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
        }
        ToggleSwitch {
          checked: root.showRemaining
          onToggled: root.persistPluginSetting("usageDisplay", root.showRemaining ? "Used" : "Remaining")
        }
      }

      RowLayout {
        width: parent.width
        spacing: Style.space(8)
        Text {
          Layout.fillWidth: true
          text: "Bar label"
          textFormat: Text.PlainText
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
        }
        PanelActionButton {
          iconText: root.glyphRefresh
          tooltipText: "All / Worst / Cycle / Icon only"
          foreground: root.foreground
          fontFamily: root.fontFamily
          onClicked: root.cycleLabel()
        }
        Text {
          Layout.preferredWidth: Style.space(72)
          horizontalAlignment: Text.AlignHCenter
          text: root.labelMode
          textFormat: Text.PlainText
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
        }
      }

      Text {
        width: parent.width
        text: "Each provider uses its existing login. Claude Code renews its saved login when needed. Warning level and the OpenRouter key command are in the widget's settings."
        textFormat: Text.PlainText
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        wrapMode: Text.WordWrap
      }
    }
  }

  // ====================================================================
  // Shared pieces
  // ====================================================================
  component LedgerRow: Item {
    id: ledgerRow
    property string label: ""
    property string value: ""
    implicitHeight: Math.max(ledgerLabel.implicitHeight, ledgerValue.implicitHeight)

    Text {
      id: ledgerLabel
      text: ledgerRow.label
      textFormat: Text.PlainText
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.body
      elide: Text.ElideRight
      anchors.left: parent.left
      anchors.right: ledgerValue.left
      anchors.rightMargin: Style.spacing.sm
      anchors.verticalCenter: parent.verticalCenter
    }
    Text {
      id: ledgerValue
      text: ledgerRow.value
      textFormat: Text.PlainText
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
    }
  }

  // Usage percentages and meters follow the display preference; warnings use consumption.
  component WindowRow: Column {
    id: wr
    property var win: ({})
    readonly property bool known: win.pct >= 0
    readonly property bool alarming: known && win.pct >= root.warnPct
    spacing: Style.space(5)

    Item {
      width: parent.width
      implicitHeight: Math.max(wrLabel.implicitHeight, wrPct.implicitHeight)

      Text {
        id: wrLabel
        text: wr.win.label || ""
        textFormat: Text.PlainText
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        elide: Text.ElideRight
        anchors.left: parent.left
        anchors.right: wrPct.left
        anchors.rightMargin: Style.spacing.sm
        anchors.verticalCenter: parent.verticalCenter
      }
      Text {
        id: wrPct
        text: wr.known ? Math.round(root.displayPct(wr.win.pct)) + (root.showRemaining ? "% remaining" : "% used") : "—"
        textFormat: Text.PlainText
        color: wr.alarming ? root.urgent : root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        font.bold: wr.alarming
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
      }
    }

    Item {
      visible: wr.known
      width: parent.width
      height: Math.max(Style.space(5), Math.round(Style.spacing.controlHeight * 0.16))

      Rectangle {
        id: trackRect
        anchors.fill: parent
        radius: height / 2
        color: root.track
      }
      Rectangle {
        anchors.left: trackRect.left
        anchors.verticalCenter: trackRect.verticalCenter
        height: trackRect.height
        radius: trackRect.radius
        width: trackRect.width * Math.max(0, Math.min(1, root.displayPct(wr.win.pct) / 100))
        color: wr.alarming ? root.urgent : root.foreground
        Behavior on width { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
      }
    }

    Text {
      readonly property string reset: root.relReset(wr.win.resetsAt, tick.date.getTime())
      readonly property string detail: wr.win.detail || ""
      visible: text !== ""
      width: parent.width
      text: detail !== "" && reset !== "" ? detail + "  ·  " + reset : (detail !== "" ? detail : reset)
      textFormat: Text.PlainText
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      elide: Text.ElideRight
    }
  }
}

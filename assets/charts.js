(function () {
  var style = getComputedStyle(document.documentElement);
  var accent = style.getPropertyValue("--accent").trim();
  var accentInk = style.getPropertyValue("--accent-ink").trim();
  var accentSoft = style.getPropertyValue("--accent-soft").trim();
  var accent2 = style.getPropertyValue("--accent2").trim();
  var accent2Soft = style.getPropertyValue("--accent2-soft").trim();
  var ink = style.getPropertyValue("--ink").trim();
  var muted = style.getPropertyValue("--muted").trim();
  var rule = style.getPropertyValue("--rule").trim();
  var bg2 = style.getPropertyValue("--bg2").trim();

  mermaid.initialize({
    startOnLoad: true,
    theme: "base",
    securityLevel: "loose",
    themeVariables: {
      primaryColor: accentSoft,
      primaryBorderColor: accent,
      primaryTextColor: accentInk,
      secondaryColor: accent2Soft,
      secondaryBorderColor: accent2,
      secondaryTextColor: ink,
      tertiaryColor: bg2,
      tertiaryBorderColor: rule,
      tertiaryTextColor: ink,
      lineColor: muted,
      textColor: ink,
      fontSize: "14px",
      clusterBkg: bg2,
      clusterBorder: rule,
      edgeLabelBackground: bg2,
      titleColor: ink
    },
    flowchart: {
      curve: "basis",
      htmlLabels: true,
      nodeSpacing: 42,
      rankSpacing: 58,
      padding: 12
    }
  });
})();

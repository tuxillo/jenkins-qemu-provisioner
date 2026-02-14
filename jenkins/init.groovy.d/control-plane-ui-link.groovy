import jenkins.model.Jenkins

def uiUrl = (System.getenv("JENKINS_CONTROL_PLANE_UI_URL") ?: "").trim()
if (!uiUrl) {
  println("--> Control-plane UI link disabled (JENKINS_CONTROL_PLANE_UI_URL empty)")
  return
}

def instance = Jenkins.get()
def markerPrefix = "[control-plane-ui]"
def snippet = "${markerPrefix} Dashboard: ${uiUrl}"
def current = instance.getSystemMessage() ?: ""

def sanitized = current
  .replaceAll(/(?m)^\[control-plane-ui\].*$/, "")
  .replaceAll(/(?s)&lt;div data-cp-ui-link=&quot;1&quot;&gt;.*?&lt;\/div&gt;/, "")
  .replaceAll(/(?s)<div data-cp-ui-link="1">.*?<\/div>/, "")
  .trim()

def next
if (sanitized.trim()) {
  next = sanitized + "\n" + snippet
} else {
  next = snippet
}

if (next != current) {
  instance.setSystemMessage(next)
  instance.save()
  println("--> Configured Jenkins system message with Control-plane UI link: ${uiUrl}")
} else {
  println("--> Control-plane UI link already configured: ${uiUrl}")
}

import jenkins.model.Jenkins

def uiUrl = (System.getenv("JENKINS_CONTROL_PLANE_UI_URL") ?: "").trim()
def instance = Jenkins.get()
def current = instance.getSystemMessage() ?: ""

def cleaned = current
  .replaceAll(/(?m)^\[control-plane-ui\].*$/, "")
  .replaceAll(/(?s)&lt;div data-cp-ui-link=&quot;1&quot;&gt;.*?&lt;\/div&gt;/, "")
  .replaceAll(/(?s)<div data-cp-ui-link="1">.*?<\/div>/, "")
  .trim()

if (!uiUrl) {
  if (cleaned != current) {
    instance.setSystemMessage(cleaned)
    instance.save()
    println("--> Control-plane UI link removed (JENKINS_CONTROL_PLANE_UI_URL empty)")
  } else {
    println("--> Control-plane UI link disabled (JENKINS_CONTROL_PLANE_UI_URL empty)")
  }
  return
}

def snippet = "<div data-cp-ui-link=\"1\"><a class=\"jenkins-button jenkins-button--primary\" href=\"${uiUrl}\" target=\"_blank\" rel=\"noopener noreferrer\">Control Plane Dashboard</a></div>"

try {
  def formatterClass = instance.pluginManager.uberClassLoader.loadClass("hudson.markup.RawHtmlMarkupFormatter")
  if (!formatterClass.isInstance(instance.getMarkupFormatter())) {
    instance.setMarkupFormatter(formatterClass.getConstructor(boolean.class).newInstance(false))
  }
} catch (ClassNotFoundException ignored) {
  println("--> Raw HTML markup formatter not available; system message will be plain text")
  snippet = "[control-plane-ui] Dashboard: ${uiUrl}"
}
def next
if (cleaned) {
  next = cleaned + "\n" + snippet
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

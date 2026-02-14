import hudson.security.FullControlOnceLoggedInAuthorizationStrategy
import hudson.security.HudsonPrivateSecurityRealm
import jenkins.model.Jenkins

def adminId = System.getenv("JENKINS_ADMIN_ID") ?: "admin"
def adminPassword = System.getenv("JENKINS_ADMIN_PASSWORD") ?: "admin"

def instance = Jenkins.get()

def realm = instance.getSecurityRealm()
if (!(realm instanceof HudsonPrivateSecurityRealm)) {
  realm = new HudsonPrivateSecurityRealm(false)
  instance.setSecurityRealm(realm)
}

try {
  realm.createAccount(adminId, adminPassword)
  println("--> Created Jenkins admin user: ${adminId}")
} catch (IllegalArgumentException ignored) {
  println("--> Jenkins admin user already exists: ${adminId}")
}

def strategy = new FullControlOnceLoggedInAuthorizationStrategy()
strategy.setAllowAnonymousRead(false)
instance.setAuthorizationStrategy(strategy)

instance.setSlaveAgentPort(50000)
instance.save()

println("--> Jenkins security configured")

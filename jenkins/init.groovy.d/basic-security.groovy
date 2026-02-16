import hudson.security.FullControlOnceLoggedInAuthorizationStrategy
import hudson.security.HudsonPrivateSecurityRealm
import jenkins.model.Jenkins

def adminId = System.getenv("JENKINS_ADMIN_ID") ?: "admin"
def adminPassword = System.getenv("JENKINS_ADMIN_PASSWORD") ?: "admin"
def agentTcpPortRaw = System.getenv("JENKINS_AGENT_TCP_PORT") ?: "-1"
def agentTcpPort = Integer.parseInt(agentTcpPortRaw)

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

instance.setSlaveAgentPort(agentTcpPort)
instance.save()

println("--> Jenkins security configured (agent TCP port: ${agentTcpPort})")

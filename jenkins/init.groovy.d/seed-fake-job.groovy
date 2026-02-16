import jenkins.model.Jenkins
import hudson.model.FreeStyleProject
import hudson.model.Label
import hudson.tasks.Shell
import hudson.tasks.BatchFile

def enabled = (System.getenv("JENKINS_BOOTSTRAP_JOB_ENABLED") ?: "true").toBoolean()
if (!enabled) {
  println("--> Fake bootstrap job disabled")
  return
}

def jobName = System.getenv("JENKINS_BOOTSTRAP_JOB_NAME") ?: "fake-ephemeral-test"
def jobLabel = System.getenv("JENKINS_BOOTSTRAP_JOB_LABEL") ?: "linux-kvm || dragonflybsd-nvmm"
def timeoutSec = (System.getenv("JENKINS_BOOTSTRAP_JOB_TIMEOUT_SEC") ?: "10") as Integer
def sleepSec = (System.getenv("JENKINS_BOOTSTRAP_JOB_SLEEP_SEC") ?: "60") as Integer
def upsert = (System.getenv("JENKINS_BOOTSTRAP_JOB_UPSERT") ?: "false").toBoolean()

def instance = Jenkins.get()

def shellScript = """
#!/bin/sh
set -eu
echo "fake job started on label ${jobLabel}"
timeout ${timeoutSec}s sh -c 'sleep ${sleepSec}' || true
""".stripIndent()

def windowsScript = """
@echo off
echo fake job started on label ${jobLabel}
timeout /t ${timeoutSec}
""".stripIndent()

def builder
if (System.getProperty("os.name", "").toLowerCase().contains("windows")) {
  builder = new BatchFile(windowsScript)
} else {
  builder = new Shell(shellScript)
}

def existing = instance.getItem(jobName)
if (existing == null) {
  def job = instance.createProject(FreeStyleProject, jobName)
  job.setAssignedLabel(Label.get(jobLabel))
  job.getBuildersList().replaceBy([builder])
  job.save()
  println("--> Created fake bootstrap freestyle job: ${jobName} (timeout ${timeoutSec}s)")
} else if (upsert && (existing instanceof FreeStyleProject)) {
  existing.setAssignedLabel(Label.get(jobLabel))
  existing.getBuildersList().replaceBy([builder])
  existing.save()
  println("--> Updated fake bootstrap job: ${jobName}")
} else {
  println("--> Fake bootstrap job already exists: ${jobName}")
}

import subprocess


def run() -> None:
    subprocess.run(["docker", "compose", "up", "-d"], check=True)
    print("Started local Jenkins via docker compose.")
    print("Next steps:")
    print("1) make init-db")
    print("2) make run")
    print("3) Register at least one host in the control-plane database")
    print("4) Run integration tests against local Jenkins and fake node-agent")


if __name__ == "__main__":
    run()

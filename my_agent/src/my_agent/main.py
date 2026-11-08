from my_agent.crew import MyAgentCrew


def run():
    MyAgentCrew().crew().kickoff(inputs={"workspace": "/home/tara/agent-workspace"})


if __name__ == "__main__":
    run()

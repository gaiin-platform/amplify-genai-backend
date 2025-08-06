import json


class AgentRegistry:
    def __init__(self):
        self.agents = {}

    def register(self, agent_id, agent_description, agent):
        self.agents[agent_id] = {
            "agent_id": agent_id,
            "description": agent_description,
            "agent": agent,
        }

    def get(self, agent_id):
        agent_entry = self.agents.get(agent_id, {})
        return agent_entry.get("agent", None)

    def get_all(self):
        return [agent["agent"] for agent in self.agents]

    def get_ids(self):
        return list(self.agents.keys())

    def get_descriptions(self):
        return [
            {"agent_id": k, "description": agent["description"]}
            for k, agent in self.agents.items()
        ]

    def __len__(self):
        return len(self.agents)

    def __iter__(self):
        return iter(self.agents.values())

    def __getitem__(self, name):
        return self.get(name)

    def __contains__(self, name):
        return name in self.agents

    def __repr__(self):
        return f"<AgentRegistry: {json.dumps(self.get_descriptions(), indent=4)}>"

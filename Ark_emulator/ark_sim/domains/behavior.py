"""Declarative state transitions and replaceable behavior plans."""
from ark_sim.rules import evaluate_expression


class BehaviorSystem:
    def __init__(self, context):
        self.ctx = context

    def transition(self, ref, state, cause=None):
        component = self.ctx.get(ref, ("behavior",), {})
        definition = self.ctx.program.definitions.get(component.get("machine"), {})
        states = definition.get("states", {})
        if state not in states:
            raise ValueError(f"unknown state {state}")
        old = component.get("state", definition.get("initial", definition.get("initial_state")))
        for effect in states.get(old, {}).get("on_exit", ()):
            self.ctx.effects.execute(ref, [ref], effect, cause=cause)
        component["state"] = state
        component["entered_at"] = self.ctx.session.time
        self.ctx.set(ref, ("behavior",), component)
        self.ctx.emit("behavior.transition", {"source": ref, "target": ref, "from": old, "to": state}, cause)
        for effect in states[state].get("on_enter", ()):
            self.ctx.effects.execute(ref, [ref], effect, cause=cause)

    def plan(self, ref):
        component = self.ctx.get(ref, ("behavior",), {})
        definition = self.ctx.program.definitions.get(component.get("machine"), {})
        provider = definition.get("provider") or definition.get("implementation", {}).get("provider")
        if provider:
            return self.ctx.provider(provider, {"source": self.ctx.entity(ref),
                "blocked_by": self.ctx.spatial.blocked_by(ref)}, definition.get("parameters", {}))
        return {"move": True, "attack": True}

    def tick(self, session):
        if self.ctx.state().get("finished"):
            return
        for entity in session.world.entities():
            ref = entity["id"]
            if not self.ctx.alive(ref):
                continue
            component = self.ctx.get(ref, ("behavior",), {})
            definition = self.ctx.program.definitions.get(component.get("machine"), {})
            state = component.get("state", definition.get("initial", definition.get("initial_state")))
            transitions = sorted(enumerate(definition.get("transitions", ())), key=lambda pair: (pair[1].get("priority", 0), pair[0]))
            for _, transition in transitions:
                if transition["from"] not in (state, "*"):
                    continue
                inputs = {"source": self.ctx.entity(ref), "target": self.ctx.entity(ref),
                          "resources": self.ctx.get(ref, ("resources",), {}), "time": session.time,
                          "state": state}
                condition = transition.get("condition", "True")
                matched = evaluate_expression(condition, inputs, transition.get("parameters", {}))
                if transition.get("condition_rule"):
                    matched = self.ctx.calc("behavior.threshold", {"resources": inputs["resources"],
                         "time": session.time, "state_parameters": transition.get("parameters", {})},
                         owner=ref, rule_id=transition["condition_rule"])
                if matched:
                    self.transition(ref, transition["to"])
                    for effect in transition.get("effects", ()):
                        self.ctx.effects.execute(ref, [ref], effect)
                    break

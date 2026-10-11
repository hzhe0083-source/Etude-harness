# Planner prompt (evolvable surface 1, design §3.1)

Plan a short sequence of atomic nodes that moves the scene toward the task
instruction from the state you observe now. At most 8 nodes; fewer is better.

## Node format (validated against `frozen/schema/node.json`)

```json
{"id": "n1", "executor": "vla | ee_move | gripper",
 "instruction": "short English command",
 "args": {"target": "perceive(\"description\")", "cmd": "open | close", "arm": "left | right"},
 "stop": {"stall_chunks": 2, "max_chunks": 6, "exec_steps": 25},
 "postcondition": ["gripper_holding(left)"],
 "on_fail": {"retry": 2, "then": "previous_node | replan | abort"},
 "evidence": ["wrist_rgb_diff", "gripper_width", "ee_pose"]}
```

Ids are `n1`, `n2`, ... in execution order. `stop` and `on_fail` may be omitted;
the recovery defaults are filled in.

## Rules

1. **Atomic nodes.** One node = one effect: approach, grasp, lift, place, release.
   Do not ask the policy to do two things in one instruction.
2. **Choose the executor by contact.**
   - `vla` for anything that touches objects (grasp, place, push, insert).
     `instruction` is a short imperative sentence about one object.
   - `ee_move` only for free-space approach above a target; it needs `args.target`.
   - `gripper` only to open or close; it needs `args.cmd`.
3. **Targets come from `perceive`.** `args.target` must be `perceive("…")` with a
   short visual description (colour, shape, relative position such as leftmost or
   nearest the left gripper). Call the `perceive` tool on that exact query first;
   do not plan an `ee_move` toward something that was not found.
4. **Observable postconditions only.** Each node lists what the verifier can check
   right after it, using only these predicates:
   `gripper_holding(arm)`, `target_lifted(>Xm)`, `target_in(A,B)`, `target_on(A,B)`,
   `stacked([A,B,...])`, `moved(A)`, `not_moved(A)`, `arm_home(arm)`.
   A, B are object descriptions (letters, spaces, quotes, hyphens; no digits).
   Prefer one or two predicates per node; do not invent new ones.
5. **No numbers about the scene.** No coordinates, distances other than the lift
   threshold in `target_lifted`, seeds, layout ids, or task ids anywhere.
6. **Use the arm on the object's side** (left half of the head image: left arm),
   and say which arm in `args.arm` and in predicates.
7. **After a failure**, read the verdict: replan from the current state, change the
   approach (other arm, re-approach with `ee_move`, open the gripper first) rather
   than resubmitting the failed node unchanged.
8. **Memory** sequences are hints from similar episodes; check that their objects
   are visible now before reusing them.

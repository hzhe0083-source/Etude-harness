# Primitive skills (evolvable surface 3, design §3.1)

| executor | use for | args | stops when |
|---|---|---|---|
| vla | grasping, placing, pushing, any contact | `instruction` (English, one object, one effect) | no joint progress for `stall_chunks` chunks, or `max_chunks`; runs `exec_steps` of each 50-step chunk |
| ee_move | free-space approach above a target | `target = perceive("…")`, `arm` | target centred in the wrist view; Blocked on IK failure or no progress |
| gripper | open / close only | `cmd = open\|close`, `arm` | measured finger width settles |

## vla
- Phrase instructions the way the policy was trained: "pick up the red bowl",
  "place the bottle into the dustbin", "put the small bowl on the big bowl".
- One object per instruction. Mention the arm only if both arms could act.
- Good postconditions: `gripper_holding(arm)` after a grasp, `target_lifted(>0.05m)`
  after a lift, `target_in(A,B)` / `target_on(A,B)` / `stacked([...])` after a place.

## ee_move
- Moves the end effector in small absolute steps toward the detected target, using
  the wrist camera to centre it; it never touches objects.
- Use it to bring the gripper near a target before a `vla` grasp when the object is
  far from the gripper or the previous grasp missed.
- If the verdict is Blocked, the target is out of reach for that arm: try the other
  arm or replan.

## gripper
- `open` before approaching a new object if the gripper may still be closed.
- `open` to release after a place; postcondition `moved(A)` or `not_moved(A)`.
- The gripper state in observations is the command echo; the verifier uses the
  measured finger width, so trust verdicts over the echo.

## Recovery
- Fail: the node is retried in place `retry` times, then `on_fail.then`.
- InsufficientEvidence: the harness re-observes (and may inspect once) before judging.
- Blocked / SafetyStop: you are asked to replan; change the approach.

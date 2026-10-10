# Checklist: before claiming a bead in a lane

The lead can move steps between lanes while a lane is working. Before claiming the next bead:

1. Re-read `.workmux/SCOPE.md`; the bead must be in this lane's queue.
2. `bd show <id>`: it must be open, or in progress with this lane as the owner. If it is in progress with notes from another lane, leave it.
3. `git fetch origin` and look at `origin/main` and the other lanes' branches for work on the same step.

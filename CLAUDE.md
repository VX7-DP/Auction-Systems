# CLAUDE.md

## Git rules

- Never commit or push unless the user explicitly allows it for that change.
- Commit author and committer must always be `VX7-DP <vx7cse@gmail.com>`. Set it before committing:
  `git config user.name "VX7-DP" && git config user.email "vx7cse@gmail.com"`
- Branch names must describe the task, in the form `<type>/<short-kebab-description>`, e.g. `feat/bid-placement-api`, `fix/auction-close-race`, `chore/add-claude-md`. Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`. Never use auto-generated names like `claude/<random-words>`; rename such a branch before pushing.
- Never add `Co-Authored-By`, `Claude-Session`, or any mention of Claude, Sonnet, Opus, Anthropic, or other AI attribution to commit messages, PR titles/descriptions, or code.

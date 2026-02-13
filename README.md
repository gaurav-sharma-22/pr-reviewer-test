# pr-reviewer-test
automated PR reviewer that reads code changes, finds real issues, comments on the PR like a human reviewer, and keeps logs — without spamming or auto-merging.

What problem are we solving? (Simple terms)

Right now:

PR reviews are slow

Review quality depends on who reviews

Same mistakes are repeated

Seniors get overloaded

We want:

A smart bot that reviews PRs

Catches bugs, security risks, bad patterns

Leaves useful comments, not noise

Helps humans review faster, not replace them

What will the system actually DO?
When a PR is created or updated:

Read the PR (title, description, changed files)

Look only at changed code

Run AI reviewers on the code

Post:

A summary comment (overall risk + checklist)

A few important inline comments (not spam)

Save everything for audit & tracking

What it will NOT do (for now)

❌ Auto-merge PRs
❌ Push code changes automatically
❌ Replace security scanners (SAST)

Core behavior (think like a flow)
PR event happens
↓
Fetch PR data + diff
↓
Break code into small chunks
↓
Decide which AI reviewers to run
↓
AI reviewers find issues
↓
Merge + clean results
↓
Post comments on GitHub
↓
Store logs & metrics

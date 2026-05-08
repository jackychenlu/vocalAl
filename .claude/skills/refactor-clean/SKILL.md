---
description: Refactor and clean code following Python best practices and SOLID principles. Use when improving code quality or reducing complexity.
disable-model-invocation: true
model: claude-sonnet-4-6
---

# Refactor and Clean Code

You are a code refactoring expert. Analyze and refactor the provided code to improve quality and maintainability without over-engineering.

## Context
This is a Python desktop app. Keep changes practical — no premature abstractions, no new dependencies, no half-finished refactors.

## Requirements
$ARGUMENTS

## Instructions

### 1. Code Analysis

**Code Smells to Look For**
- Long methods (>50 lines)
- Large classes (>500 lines with too many responsibilities)
- Duplicate code blocks
- Dead code and unused variables
- Complex nested conditionals (>3 levels)
- Magic numbers and hardcoded values
- Poor or misleading naming

**SOLID Violations**
- Single Responsibility: methods doing too many things
- Dependency Inversion: hardcoded dependencies vs constructor injection

**Performance Issues**
- Unnecessary object creation in loops
- Blocking operations on the main thread (tkinter freeze risk)

### 2. Refactoring Strategy

Prioritize by impact:

**Immediate (High Impact, Low Risk)**
- Extract magic numbers to named constants
- Improve variable and method names
- Break long methods into smaller focused ones
- Remove dead code

**Structural (Medium Impact, Medium Risk)**
- Extract repeated logic into helper methods
- Separate GUI event handlers from business logic

**Avoid**
- Creating new abstractions without a clear need (3+ usages)
- Changing the public API of service classes
- Splitting files without reducing complexity

### 3. Output

For each proposed change:
- Show the before and after code
- Explain why this is better
- Note any risks or dependencies

Apply only what is explicitly asked for. Do not refactor code not mentioned in `$ARGUMENTS`.

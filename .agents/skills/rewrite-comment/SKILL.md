---
name: rewrite-comment
description: Rewrite a comment so a newcomer to the system can build a mental model from it.
disable-model-invocation: true
argument-hint: "[which comment: top of the file | these lines | all of them | ...]"
---

Target: $ARGUMENTS

If no target was given, assume the comment at the top of the module currently in focus.

The targeted comment is not understandable. Think of somebody who comes new to the system. Help them build a mental model of what's going on in this file.

Don't be afraid to use a more elaborate style. Do not follow the current style.

Your comment doesn't need to have the same content as the current one. Use it only as hints. Read the current file and its surrounding context so that you understand 
what's going on well enough to help a newcomer build a mental model of the system.

"""Reading an LLM's JSON answer pasted by hand (the manual "Write it myself" flows of every channel)."""

from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError


def repair_json(text: str) -> str:
    """Fix what LLMs get wrong in JSON by hand: a " inside a string left unescaped (a quote like ("Cerdagne")),
    a raw line break inside a string, and a comma before } or ]. A " ends the string only when the next
    character that isn't a space is , } ] or : (or the text ends)."""
    out: list[str] = []
    in_string = False
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if not in_string:
            if ch == '"':
                in_string = True
            elif ch in "}]":
                while out and out[-1].isspace():
                    out.pop()
                if out and out[-1] == ",":
                    out.pop()
            out.append(ch)
        elif ch == "\\" and i + 1 < n:
            out.append(text[i:i + 2])
            i += 1
        elif ch == '"':
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j >= n or text[j] in ",}]:":
                in_string = False
                out.append(ch)
            else:
                out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def parse(text: str, schema: type[BaseModel], error: type[Exception] = ValueError):
    """The JSON object in an LLM's answer (a code fence or words around it are fine; so are the usual
    hand-written JSON slips, see repair_json)."""
    text = (text or "").strip()
    first, last = text.find("{"), text.rfind("}")
    if first < 0 or last <= first:
        raise error("There is no JSON object in the answer: ask the LLM to answer with the JSON only")
    body = text[first:last + 1]
    try:
        json.loads(body)
    except ValueError:
        body = repair_json(body)
    try:
        return schema.model_validate_json(body)
    except ValidationError as err:
        raise error("The answer isn't in the right format: " + "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in err.errors()[:4])) from None

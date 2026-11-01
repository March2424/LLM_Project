"""Parse a JSON response, allowing a single complete Markdown JSON code fence."""
import json


def parse_json_reply(text: str):
    value=text.strip()
    lines=value.splitlines()
    if len(lines)>=3 and lines[0].casefold() in {'```json','```'} and lines[-1]=='```':
        value='\n'.join(lines[1:-1])
    return json.loads(value)

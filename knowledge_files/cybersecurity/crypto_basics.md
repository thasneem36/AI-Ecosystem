# Common CTF Encodings

## Base64
- **Identify by:** A-Z, a-z, 0-9, +, / characters; ends with `=` or `==` padding
- Example: `dGVzdA==` decodes to `test`
- Decode: `echo "dGVzdA==" | base64 -d`
- Note: URL-safe variant uses `-` and `_` instead of `+` and `/`

## Hex
- **Identify by:** only 0-9 and a-f characters, even character count
- Example: `74657374` decodes to `test`
- Decode: `echo "74657374" | xxd -r -p`  or  `python3 -c "print(bytes.fromhex('74657374'))"`

## ROT13
- **Identify by:** looks like English words but letters are shifted 13 places; often used in hints
- Decode: `echo "grfg" | tr 'A-Za-z' 'N-ZA-Mn-za-m'`

## Binary / Octal
- Binary: groups of 8 bits (e.g. `01110100 01100101 01110011 01110100`)
- Decode: `python3 -c "print(''.join(chr(int(b,2)) for b in '01110100 01100101'.split()))"`

## Quick identification tips
- Try CyberChef (cyberchef.org) — paste ciphertext and use **Magic** mode
- `strings <file>` often reveals embedded encoded data in binaries
- `=` padding and alphabet of 64 chars → Base64; only hex chars → Hex; shifted English → ROT13

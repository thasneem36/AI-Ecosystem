# nmap Cheatsheet

## Essential scans

Version + default scripts (best starting point):
```
nmap -sC -sV <ip>
```

All 65 535 TCP ports (slow but finds non-standard ports):
```
nmap -p- -T4 <ip>
```

Full recon in one command (HTB/THM recommended first scan):
```
nmap -sC -sV -p- -T4 <ip>
```

## Scan types
- `-sS` SYN/stealth scan — half-open, requires root, less logging; **default when root**
- `-sT` Connect scan — full TCP handshake, works without root, noisier
- `-sU` UDP scan — slow; use `-sU -p 53,161,500,1194` for common UDP services

## Useful flags
- `-A`  Aggressive: OS detect + version + scripts + traceroute
- `-O`  OS fingerprinting (requires root)
- `-Pn` Skip host discovery (useful when ICMP is blocked)
- `--script vuln`  Run vulnerability detection NSE scripts
- `-oN out.txt`  Save output to file

## Output interpretation
- `open` — port is accepting connections
- `filtered` — firewall is blocking; not necessarily closed
- `closed` — port is reachable but nothing listening

## Common CTF ports to note
22 (SSH), 80/443 (HTTP/S), 21 (FTP), 3306 (MySQL), 5432 (Postgres), 8080 (alt HTTP)

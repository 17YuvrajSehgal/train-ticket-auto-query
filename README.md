# train-ticket-auto-query

Train Ticket Auto Query Python Scripts

## How to use

```python
import logging
from queries import Query
from scenarios import query_and_preserve

# login train-ticket and store the cookies
q = Query(url)
if not q.login():
    logging.fatal('login failed')

# execute scenario on current user
query_and_preserve(q)

# or execute query directly
q.query_high_speed_ticket()
```

### 60 seconds, 150 workers, mixed endpoints (recommended)
```
python3 generateload.py --url http://localhost:18888 --workers 150 --duration 60 --scenarios mixed -q
```

# Or use the script
```
./generateload.sh high
```
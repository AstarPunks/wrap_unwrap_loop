# Environment Setup

```
$ python -V
Python 3.9.5
$ python -m venv .venv
$ source .venv/bin/activate
$ pip install web3 python-dotenv
```

# carete .env file

```
$ cat .env 
RPC_URL=https://rpc.soneium.org
PRIVATE_KEY=0x[secret key]
FROM_ADDRESS=0x[wallet addr]
```

# How to use

 * Run once

```
$ python ./wrap_unwrap_loop.py --once
```

 * Specify number of rounds

```
$ python ./wrap_unwrap_loop.py --rounds 10000
```
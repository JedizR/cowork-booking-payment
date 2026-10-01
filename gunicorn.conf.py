# ponytail: workers = 2 with one DB connection per worker is the ceiling (two
# requests in flight per container); move to psycopg_pool when it is not enough.
workers = 2
# Request log to stdout. %(U)s is the path without the query string, and the
# referer is left out, because URLs can carry values that must not be logged.
accesslog = "-"
access_log_format = '%(h)s %(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s %(M)sms "%(a)s"'

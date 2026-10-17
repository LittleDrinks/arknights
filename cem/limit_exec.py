"""Child-only limits, before exec; safe to launch from threaded supervisors."""
import os
import resource
import sys

resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
# Bound each raw/audit/log file. Aggregate output is separately supervised.
resource.setrlimit(resource.RLIMIT_FSIZE, (262144, 262144))
os.execv(sys.argv[1], sys.argv[1:])

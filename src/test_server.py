#!/usr/bin/env python
import os
import socket
from spark.async import Future
from spark.session import Session, SparkSession

print "PID: %i" % os.getpid()
s = SparkSession()
remoteAddr = s.listen(("", 4550), None)[0]
print "Connected to %s" % repr(remoteAddr)
s.join()
print "Disconnected"
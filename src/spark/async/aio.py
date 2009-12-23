# -*- coding: utf-8 -*-
#
# Copyright (C) 2009 Pierre-André Saulais <pasaulais@free.fr>
#
# This file is part of the Spark File-transfer Tool.
#
# Spark is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# Spark is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Spark; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

__all__ = ["Reactor"]

class Reactor(object):
    """
    Manages asynchronous I/O and non-IO operations.
    
    All callbacks (callable passed to the invoke function and I/O Future callbacks)
    are guaranteed to be executed on the same thread ("reactor thread").
    """
    
    def launch_thread(self):
        """ Start a background thread to run the reactor. """
        raise NotImplementedError()
    
    def run(self):
        """ Run the reactor on the current thread. """
        raise NotImplementedError()
    
    def close(self):
        """ Close the reactor, terminating all pending operations. """
        raise NotImplementedError()
    
    def socket(self, family, type, proto):
        """ Create a socket that uses the reactor to do asynchronous I/O. """
        raise NotImplementedError()
    
    def open(self, file, mode=None):
        """ Open a file that uses the reactor to do asynchronous I/O. """
        raise NotImplementedError()
    
    def pipe(self):
        """ Create a pipe that uses the reactor to do asynchronous I/O. """
        raise NotImplementedError()
    
    def pipes(self):
        """ Create two connected, bidirectionnal pipes. """
        r1, w1 = self.pipe()
        r2, w2 = self.pipe()
        return PipeWrapper(r1, w2), PipeWrapper(r2, w1)
    
    def send(self, fun, *args, **kwargs):
        """
        Invoke a callable on the reactor's thread and return its result through a future.
        """
        raise NotImplementedError()
    
    def post(self, fun, *args, **kwargs):
        """
        Submit a callable to be invoked on the reactor's thread later.
        
        Unlike send(), this method doesn't return a future.
        """
        raise NotImplementedError()
    
    _types = []
    
    @classmethod
    def available(cls):
        """ Return the list of all available reactor types. """
        return cls._types
    
    @classmethod
    def addType(cls, type):
        """ Add a reactor type to the list of available types"""
        cls._types.append(type)
    
    @classmethod
    def default(cls):
        """ Return the default reactor type. """
        if len(cls._types) == 0:
            raise Exception("No type of reactor is available")
        else:
            return cls._types[0]
    
    @classmethod
    def create(cls):
        """ Create a reactor of the default type. """
        return cls.default()()

class PipeWrapper(object):
    """
    File-like socket encapsulating both the reading end of a pipe and
    the writing end of another pipe, much like a socket.
    """
    def __init__(self, r, w):
        self.r = r
        self.w = w
    
    def beginRead(self, size):
        return self.r.beginRead(size)
    
    def beginWrite(self, data):
        return self.w.beginWrite(data)
    
    def read(self, size):
        return self.r.read(size)
    
    def write(self, data):
        return self.w.write(data)
    
    def close(self):
        self.r.close()
        self.w.close()

# try to import known types of reactors
try:
    import spark.async.pollreactor
except ImportError:
    pass

try:
    import spark.async.iocpreactor
except ImportError:
    pass

#import spark.async.threadpoolreactor

#!/usr/bin/env bash
# Android Device Testing Framework ("dtf")
# Copyright 2013-2015 Jake Valletta (@jake_valletta)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# This this is a wraper script for "baksmali".

dtf_baksmali()
{
    jarfile=${DTF_INCLUDED}/smali/baksmali-2.1.0-3e265038.jar

    if [ ! -r "$jarfile" ]; then 
        echo "dtf_baksmali: can't find $jarfile"
        return 1
    fi

    javaOpts="-Xmx256M"

    while expr "x$1" : 'x-J' >/dev/null; do
        opt=`expr "$1" : '-J\(.*\)'`
        javaOpts="${javaOpts} -${opt}"
        shift
    done

    jarpath="$jarfile"

    java $javaOpts -jar "$jarpath" "$@"
    return $?
}

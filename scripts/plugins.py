#!/usr/bin/env python
# | Copyright 2016-2017 Karlsruhe Institute of Technology
# |
# | Licensed under the Apache License, Version 2.0 (the "License");
# | you may not use this file except in compliance with the License.
# | You may obtain a copy of the License at
# |
# |     http://www.apache.org/licenses/LICENSE-2.0
# |
# | Unless required by applicable law or agreed to in writing, software
# | distributed under the License is distributed on an "AS IS" BASIS,
# | WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# | See the License for the specific language governing permissions and
# | limitations under the License.

import sys
from gc_scripts import Plugin, ScriptOptions, display_plugin_list, get_plugin_list
from python_compat import lmap


def _main():
	parser = ScriptOptions(usage='%s [OPTIONS] <BasePlugin>')
	parser.add_bool(None, 'p', 'parents', default=False, help='Show plugin parents')
	options = parser.script_parse()
	if len(options.args) != 1:
		parser.exit_with_usage()
	pname = options.args[0]
	if options.opts.parents:
		cls_info = lmap(lambda cls: {'Name': cls.__name__, 'Alias': str.join(', ', cls.alias_list)},
			Plugin.get_class(pname).iter_class_bases())
		display_plugin_list(cls_info, sort=False, title='Parents of plugin %r' % pname)
	else:
		display_plugin_list(get_plugin_list(pname), title='Available plugins of type %r' % pname)


if __name__ == '__main__':
	sys.exit(_main())

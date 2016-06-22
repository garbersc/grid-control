# | Copyright 2007-2016 Karlsruhe Institute of Technology
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

import time, logging
from grid_control.gc_plugin import ConfigurablePlugin
from grid_control.utils.data_structures import makeEnum
from hpfwk import AbstractError, NestedException
from python_compat import irange

class JobError(NestedException):
	pass

class Job(object):
	def __init__(self):
		self.state = Job.INIT
		self.attempt = 0
		self.history = {}
		self.wmsId = None
		self.submitted = 0
		self.changed = 0
		self.dict = {}


	def get_dict(self):
		return {'id': self.wmsId, 'status': Job.enum2str(self.state),
			'attempt': self.attempt, 'submitted': self.submitted, 'changed': self.changed}


	def set(self, key, value):
		self.dict[key] = value


	def get(self, key, default = None):
		return self.dict.get(key, default)


	def update(self, state):
		self.state = state
		self.changed = time.time()
		self.history[self.attempt] = self.dict.get('dest', 'N/A')


	def assignId(self, wmsId):
		self.dict['legacy'] = None # Legacy support
		self.wmsId = wmsId
		self.attempt = self.attempt + 1
		self.submitted = time.time()

makeEnum(['INIT', 'SUBMITTED', 'DISABLED', 'READY', 'WAITING', 'QUEUED', 'ABORTED',
		'RUNNING', 'CANCEL', 'UNKNOWN', 'CANCELLED', 'DONE', 'FAILED', 'SUCCESS'], Job)


class JobClassHolder(object):
	def __init__(self, *states):
		self.states = []
		for state in states:
			if isinstance(state, JobClassHolder):
				self.states.extend(state.states)
			else:
				self.states.append(state)


class JobClass(object):
	ATWMS = JobClassHolder(Job.SUBMITTED, Job.WAITING, Job.READY, Job.QUEUED, Job.UNKNOWN)
	PROCESSING = JobClassHolder(Job.SUBMITTED, Job.WAITING, Job.READY, Job.QUEUED, Job.UNKNOWN, Job.RUNNING)
	CANCEL = JobClassHolder(Job.CANCEL)
	DONE = JobClassHolder(Job.DONE)
	DISABLED = JobClassHolder(Job.DISABLED)
	ENDSTATE = JobClassHolder(Job.SUCCESS, Job.DISABLED)
	PROCESSED = JobClassHolder(Job.SUCCESS, Job.FAILED, Job.CANCELLED, Job.ABORTED)
	READY = JobClassHolder(Job.INIT, Job.FAILED, Job.ABORTED, Job.CANCELLED)
	SUCCESS = JobClassHolder(Job.SUCCESS)


class JobDB(ConfigurablePlugin):
	def __init__(self, config, jobLimit = -1, jobSelector = None):
		ConfigurablePlugin.__init__(self, config)
		self._log = logging.getLogger('jobs')
		(self._jobLimit, self._alwaysSelector) = (jobLimit, jobSelector)
		(self._defaultJob, self._workPath) = (Job(), config.getWorkPath())

	def setJobLimit(self, value):
		self._jobLimit = value

	def __len__(self):
		return self._jobLimit

	def getWorkPath(self): # TODO: only used by report class
		return self._workPath

	def getJobsIter(self, jobSelector = None, subset = None):
		if subset is None:
			subset = irange(self._jobLimit)
		if jobSelector and self._alwaysSelector:
			select = lambda *args: jobSelector(*args) and self._alwaysSelector(*args)
		elif jobSelector or self._alwaysSelector:
			select = jobSelector or self._alwaysSelector
		else:
			for jobNum in subset:
				yield jobNum
			raise StopIteration
		for jobNum in subset:
			if select(jobNum, self.getJobTransient(jobNum)):
				yield jobNum

	def getJobs(self, jobSelector = None, subset = None):
		return list(self.getJobsIter(jobSelector, subset))

	def getJobsN(self, jobSelector = None, subset = None):
		return len(self.getJobs(jobSelector, subset)) # fastest method! (iter->list written in C)

	def getJob(self, jobNum):
		raise AbstractError

	def getJobTransient(self, jobNum):
		raise AbstractError

	def getJobPersistent(self, jobNum):
		raise AbstractError

	def commit(self, jobNum, jobObj):
		raise AbstractError


import os, time, fnmatch
from grid_control import utils
from grid_control.job_db import Job, JobDB, JobError
from grid_control.utils.file_objects import SafeFile
from python_compat import irange, sorted

class TextFileJobDB(JobDB):
	def __init__(self, config, jobLimit = -1, jobSelector = None):
		JobDB.__init__(self, config, jobLimit, jobSelector)
		self._dbPath = config.getWorkPath('jobs')
		self._fmt = utils.DictFormat(escapeString = True)
		self._jobMap = self._readJobs(self._jobLimit)
		if self._jobLimit < 0 and len(self._jobMap) > 0:
			self._jobLimit = max(self._jobMap) + 1


	def _serialize_job_obj(self, job_obj):
		data = dict(job_obj.dict)
		data['status'] = Job.enum2str(job_obj.state)
		data['attempt'] = job_obj.attempt
		data['submitted'] = job_obj.submitted
		data['changed'] = job_obj.changed
		for key, value in job_obj.history.items():
			data['history_' + str(key)] = value
		if job_obj.wmsId is not None:
			data['id'] = job_obj.wmsId
			if job_obj.dict.get('legacy', None): # Legacy support
				data['id'] = job_obj.dict.pop('legacy')
		return data


	def _create_job_obj(self, name, data):
		try:
			job = Job()
			job.state = Job.str2enum(data.get('status'), Job.FAILED)

			if 'id' in data:
				if not data['id'].startswith('WMSID'): # Legacy support
					data['legacy'] = data['id']
					if data['id'].startswith('https'):
						data['id'] = 'WMSID.GLITEWMS.%s' % data['id']
					else:
						wmsId, backend = tuple(data['id'].split('.', 1))
						data['id'] = 'WMSID.%s.%s' % (backend, wmsId)
				job.wmsId = data['id']
			for key in ['attempt', 'submitted', 'changed']:
				if key in data:
					setattr(job, key, data[key])
			if 'runtime' not in data:
				if 'submitted' in data:
					data['runtime'] = time.time() - float(job.submitted)
				else:
					data['runtime'] = 0
			for key in irange(1, job.attempt + 1):
				if ('history_' + str(key)).strip() in data:
					job.history[key] = data['history_' + str(key)]

			for i in ('wmsId', 'status'):
				try:
					del data[i]
				except Exception:
					pass
			job.dict = data
		except Exception:
			raise JobError('Unable to parse data in %s:\n%r' % (name, data))
		return job


	def _load_job(self, name):
		try:
			data = self._fmt.parse(open(name))
		except Exception:
			raise JobError('Invalid format in %s' % name)
		return self._create_job_obj(name, data)


	def _readJobs(self, jobLimit):
		try:
			if not os.path.exists(self._dbPath):
				os.mkdir(self._dbPath)
		except Exception:
			raise JobError("Problem creating work directory '%s'" % self._dbPath)

		candidates = []
		for jobFile in fnmatch.filter(os.listdir(self._dbPath), 'job_*.txt'):
			try: # 2xsplit is faster than regex
				jobNum = int(jobFile.split(".")[0].split("_")[1])
			except Exception:
				continue
			candidates.append((jobNum, jobFile))

		(jobMap, maxJobs) = ({}, len(candidates))
		activity = utils.ActivityLog('Reading job infos ...')
		idx = 0
		for (jobNum, jobFile) in sorted(candidates):
			idx += 1
			if (jobLimit >= 0) and (jobNum >= jobLimit):
				self._log.info('Stopped reading job infos at job #%d out of %d available job files', jobNum, len(candidates))
				break
			jobObj = self._load_job(os.path.join(self._dbPath, jobFile))
			jobMap[jobNum] = jobObj
			if idx % 100 == 0:
				activity.finish()
				activity = utils.ActivityLog('Reading job infos ... %d [%d%%]' % (idx, (100.0 * idx) / maxJobs))
		activity.finish()
		return jobMap


	def getJob(self, jobNum):
		return self._jobMap.get(jobNum)


	def getJobTransient(self, jobNum):
		return self._jobMap.get(jobNum, self._defaultJob)


	def getJobPersistent(self, jobNum):
		return self._jobMap.get(jobNum, Job())


	def commit(self, jobNum, jobObj):
		fp = SafeFile(os.path.join(self._dbPath, 'job_%d.txt' % jobNum), 'w')
		fp.writelines(self._fmt.format(self._serialize_job_obj(jobObj)))
		fp.close()
		self._jobMap[jobNum] = jobObj

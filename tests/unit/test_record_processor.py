# Copyright (c) 2013 Mirantis Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import mock
import testtools

from stackalytics.processor import record_processor
from stackalytics.processor import runtime_storage
from stackalytics.processor import utils


LP_URI = 'https://api.launchpad.net/1.0/people/?ws.op=getByEmail&email=%s'


def _make_users(users):
    users_index = {}
    for user in users:
        if 'user_id' in user:
            users_index[user['user_id']] = user
        if 'launchpad_id' in user:
            users_index[user['launchpad_id']] = user
        for email in user['emails']:
            users_index[email] = user
    return users_index


def _make_companies(companies):
    domains_index = {}
    for company in companies:
        for domain in company['domains']:
            domains_index[domain] = company['company_name']
    return domains_index


class TestRecordProcessor(testtools.TestCase):
    def setUp(self):
        super(TestRecordProcessor, self).setUp()

        companies = [
            {
                'company_name': 'SuperCompany',
                'domains': ['super.com', 'super.no']
            },
            {
                "domains": ["nec.com", "nec.co.jp"],
                "company_name": "NEC"
            },
            {
                'company_name': '*independent',
                'domains': ['']
            },
        ]

        self.user = {
            'user_id': 'john_doe',
            'launchpad_id': 'john_doe',
            'user_name': 'John Doe',
            'emails': ['johndoe@gmail.com', 'jdoe@super.no'],
            'companies': [
                {'company_name': '*independent',
                 'end_date': 1234567890},
                {'company_name': 'SuperCompany',
                 'end_date': 0},
            ]
        }
        self.get_users = mock.Mock(return_value=[
            self.user,
        ])

        releases = [
            {
                'release_name': 'prehistory',
                'end_date': utils.date_to_timestamp('2011-Apr-21')
            },
            {
                'release_name': 'Diablo',
                'end_date': utils.date_to_timestamp('2011-Sep-08')
            },
            {
                'release_name': 'Zoo',
                'end_date': utils.date_to_timestamp('2035-Sep-08')
            },
        ]

        def get_by_key(table):
            if table == 'companies':
                return _make_companies(companies)
            elif table == 'users':
                return _make_users(self.get_users())
            elif table == 'releases':
                return releases
            else:
                raise Exception('Wrong table %s' % table)

        p_storage = mock.Mock(runtime_storage.RuntimeStorage)
        p_storage.get_by_key = mock.Mock(side_effect=get_by_key)

        self.runtime_storage = p_storage
        self.commit_processor = record_processor.RecordProcessor(p_storage)
        self.read_json_from_uri_patch = mock.patch(
            'stackalytics.processor.utils.read_json_from_uri')
        self.read_json = self.read_json_from_uri_patch.start()

    def tearDown(self):
        super(TestRecordProcessor, self).tearDown()
        self.read_json_from_uri_patch.stop()

    def _generate_commits(self, email='johndoe@gmail.com', date=1999999999):
        yield {
            'record_type': 'commit',
            'commit_id': 'de7e8f297c193fb310f22815334a54b9c76a0be1',
            'author_name': 'John Doe',
            'author_email': email,
            'date': date,
            'lines_added': 25,
            'lines_deleted': 9,
            'release_name': 'havana',
        }

    def test_get_company_by_email_mapped(self):
        email = 'jdoe@super.no'
        res = self.commit_processor._get_company_by_email(email)
        self.assertEquals('SuperCompany', res)

    def test_get_company_by_email_with_long_suffix_mapped(self):
        email = 'man@mxw.nes.nec.co.jp'
        res = self.commit_processor._get_company_by_email(email)
        self.assertEquals('NEC', res)

    def test_get_company_by_email_with_long_suffix_mapped_2(self):
        email = 'man@mxw.nes.nec.com'
        res = self.commit_processor._get_company_by_email(email)
        self.assertEquals('NEC', res)

    def test_get_company_by_email_not_mapped(self):
        email = 'foo@boo.com'
        res = self.commit_processor._get_company_by_email(email)
        self.assertEquals(None, res)

    def test_update_commit_existing_user(self):
        commit_generator = self._generate_commits()
        commit = list(self.commit_processor.process(commit_generator))[0]

        self.assertEquals('SuperCompany', commit['company_name'])
        self.assertEquals('john_doe', commit['launchpad_id'])

    def test_update_commit_existing_user_old_job(self):
        commit_generator = self._generate_commits(date=1000000000)
        commit = list(self.commit_processor.process(commit_generator))[0]

        self.assertEquals('*independent', commit['company_name'])
        self.assertEquals('john_doe', commit['launchpad_id'])

    def test_update_commit_existing_user_new_email_known_company(self):
        """
        User is known to LP, his email is new to us, and maps to other company
        Should return other company instead of those mentioned in user db
        """
        email = 'johndoe@nec.co.jp'
        commit_generator = self._generate_commits(email=email)
        launchpad_id = 'john_doe'
        self.read_json.return_value = {'name': launchpad_id,
                                       'display_name': launchpad_id}
        user = self.user.copy()
        # tell storage to return existing user
        self.get_users.return_value = [user]

        commit = list(self.commit_processor.process(commit_generator))[0]

        self.runtime_storage.set_by_key.assert_called_with('users', mock.ANY)
        self.read_json.assert_called_once_with(LP_URI % email)
        self.assertIn(email, user['emails'])
        self.assertEquals('NEC', commit['company_name'])
        self.assertEquals(launchpad_id, commit['launchpad_id'])

    def test_update_commit_existing_user_new_email_unknown_company(self):
        """
        User is known to LP, but his email is new to us. Should match
        the user and return current company
        """
        email = 'johndoe@yahoo.com'
        commit_generator = self._generate_commits(email=email)
        launchpad_id = 'john_doe'
        self.read_json.return_value = {'name': launchpad_id,
                                       'display_name': launchpad_id}
        user = self.user.copy()
        # tell storage to return existing user
        self.get_users.return_value = [user]

        commit = list(self.commit_processor.process(commit_generator))[0]

        self.runtime_storage.set_by_key.assert_called_with('users', mock.ANY)
        self.read_json.assert_called_once_with(LP_URI % email)
        self.assertIn(email, user['emails'])
        self.assertEquals('SuperCompany', commit['company_name'])
        self.assertEquals(launchpad_id, commit['launchpad_id'])

    def test_update_commit_new_user(self):
        """
        User is known to LP, but new to us
        Should add new user and set company depending on email
        """
        email = 'smith@nec.com'
        commit_generator = self._generate_commits(email=email)
        launchpad_id = 'smith'
        self.read_json.return_value = {'name': launchpad_id,
                                       'display_name': 'Smith'}
        self.get_users.return_value = []

        commit = list(self.commit_processor.process(commit_generator))[0]

        self.read_json.assert_called_once_with(LP_URI % email)
        self.assertEquals('NEC', commit['company_name'])
        self.assertEquals(launchpad_id, commit['launchpad_id'])

    def test_update_commit_new_user_unknown_to_lb(self):
        """
        User is new to us and not known to LP
        Should set user name and empty LPid
        """
        email = 'inkognito@avs.com'
        commit_generator = self._generate_commits(email=email)
        self.read_json.return_value = None
        self.get_users.return_value = []

        commit = list(self.commit_processor.process(commit_generator))[0]

        self.read_json.assert_called_once_with(LP_URI % email)
        self.assertEquals('*independent', commit['company_name'])
        self.assertEquals(None, commit['launchpad_id'])

    def test_update_commit_invalid_email(self):
        """
        User's email is malformed
        """
        email = 'error.root'
        commit_generator = self._generate_commits(email=email)
        self.read_json.return_value = None
        self.get_users.return_value = []

        commit = list(self.commit_processor.process(commit_generator))[0]

        self.assertEquals(0, self.read_json.called)
        self.assertEquals('*independent', commit['company_name'])
        self.assertEquals(None, commit['launchpad_id'])

    def _generate_record_commit(self):
        yield {'commit_id': u'0afdc64bfd041b03943ceda7849c4443940b6053',
               'lines_added': 9,
               'module': u'stackalytics',
               'record_type': 'commit',
               'message': u'Closes bug 1212953\n\nChange-Id: '
                          u'I33f0f37b6460dc494abf2520dc109c9893ace9e6\n',
               'subject': u'Fixed affiliation of Edgar and Sumit',
               'loc': 10,
               'user_id': u'john_doe',
               'primary_key': u'0afdc64bfd041b03943ceda7849c4443940b6053',
               'author_email': u'jdoe@super.no',
               'company_name': u'SuperCompany',
               'record_id': 6,
               'lines_deleted': 1,
               'week': 2275,
               'blueprint_id': None,
               'bug_id': u'1212953',
               'files_changed': 1,
               'author_name': u'John Doe',
               'date': 1376737923,
               'launchpad_id': u'john_doe',
               'branches': set([u'master']),
               'change_id': u'I33f0f37b6460dc494abf2520dc109c9893ace9e6',
               'release': u'havana'}

    def test_update_record_no_changes(self):
        commit_generator = self._generate_record_commit()
        release_index = {'0afdc64bfd041b03943ceda7849c4443940b6053': 'havana'}

        updated = list(self.commit_processor.update(commit_generator,
                                                    release_index))

        self.assertEquals(0, len(updated))

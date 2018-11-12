import logging
import queue
import threading

import attr
import click
import requests

logging.basicConfig()
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def get(arr, idx, default=None):
    if len(arr) > idx:
        return arr[idx]
    return default


@attr.s
class Update(object):
    code = attr.ib()

    @classmethod
    def from_dict(cls, data):
        code = data[0]
        logger.info(data)
        if code == 4:
            message_update = MessageUpdate(code=code,
                                           message_id=data[1],
                                           flags=data[2],
                                           peer_id=get(data, 3),
                                           timestamp=get(data, 4),
                                           text=get(data, 5),
                                           extra=get(data, 6)
                                           )
        else:
            message_update = cls(code=code)
        return message_update


@attr.s
class MessageUpdate(Update):
    message_id = attr.ib()
    flags = attr.ib()
    peer_id = attr.ib(default=None)
    timestamp = attr.ib(default=None)
    subject = attr.ib(default=None)
    text = attr.ib(default=None)
    extra = attr.ib(default=None)

    def is_chat(self):
        return self.peer_id > 2000000000

    def chat_id(self):
        return self.peer_id - 2000000000

    def is_outbox(self):
        return self.flags & 2 == 2

    def sender(self):
        if self.is_chat():
            if self.extra:
                sender = self.extra.get('from', None)
                return int(sender) if sender else None
            return None
        return self.peer_id


@attr.s
class Attachment(object):
    attachment_type = attr.ib()
    owner_id = attr.ib()
    media_id = attr.ib()

    def to_string(self):
        return '{}{}_{}'.format(self.attachment_type,
                                self.owner_id,
                                self.media_id)


class VkApi(object):
    BASE_VK_API_URL = 'https://api.vk.com/method/'

    def __init__(self, access_token):
        self._access_token = access_token

    def _make_request(self, method_name, params):
        url = '{}{}'.format(self.BASE_VK_API_URL, method_name)
        logger.info('Sending {} with {}'.format(method_name, params))
        parameters = {'access_token': self._access_token}
        parameters.update(params)
        return requests.get(url, params=parameters)

    @staticmethod
    def _process_response(response, cls):
        response.raise_for_status()
        response = response.json()
        error = response.get('error')
        content = response.get('response')
        if error is None:
            if cls is None:
                return content, None
            return cls.from_dict(content), None
        return None, error

    def get_long_poll_server(self, need_pts=False, version='5.87'):
        method_name = 'messages.getLongPollServer'
        params = {
            'need_pts': int(need_pts),
            'v': version,
        }
        response = self._make_request(method_name, params)
        return self._process_response(response, LongPollServer)

    @staticmethod
    def get_long_poll_update(long_poll_server, mode=0):
        params = long_poll_server.to_dict()
        params.update({
            'act': 'a_check',
            'wait': 25,
            'mode': mode,
            'version': 2
        })
        url = 'https://{}'.format(long_poll_server.server)
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        return data['ts'], [Update.from_dict(u) for u in data['updates']]

    def delete(self, *message_ids, version='5.87'):
        method_name = 'messages.delete'
        message_ids = ','.join(map(str, message_ids))
        params = {
            'message_ids': message_ids,
            'v': version,
        }
        response = self._make_request(method_name, params)
        return self._process_response(response, None)

    def send(self, peer_id, message=None, *attachments, version='5.87'):
        method_name = 'messages.send'
        params = {
            'peer_id': peer_id,
            'v': version,
        }
        if message:
            params['message'] = message
        attachments = ','.join(a.to_string() for a in attachments)
        if attachments:
            params['attachment'] = attachments
        logger.info('Sending: {}'.format(params))
        response = self._make_request(method_name, params)
        return self._process_response(response, None)

    def create_poll(self,
                    question,
                    *answers,
                    is_anonymous=False,
                    is_multiple=False,
                    end_date=0,
                    owner_id=None,
                    photo_id=None,
                    background_id=None,
                    version='5.87',
                    ):
        method_name = 'polls.create'
        assert(len(answers) > 0 and len(answers) <= 10)
        answers = ('"{}"'.format(answer) for answer in answers)
        # TODO: add params
        params = {
            'question': question,
            'add_answers': '[{}]'.format(','.join(answers)),
            'v': version,
        }
        response = self._make_request(method_name, params)
        return self._process_response(response, None)

    def get_users(self, *users_ids, fields=None, name_case=None,
                  version='5.87'):
        fields = fields or ['screen_name']
        method_name = 'users.get'
        params = {
            'users_ids': ','.join(map(str, users_ids)),
            'fields': ','.join(fields),
            'v': version,
        }
        response = self._make_request(method_name, params)
        return self._process_response(response, None)

    def get_chat(self, *chat_ids, fields=None, name_case=None, version='5.87'):
        fields = fields or ['screen_name']
        method_name = 'messages.getChat'
        params = {
            'chat_ids': ','.join(map(str, chat_ids)),
            'fields': ','.join(fields),
            'v': version,
        }
        response = self._make_request(method_name, params)
        return self._process_response(response, None)


@attr.s
class LongPollServer(object):
    key = attr.ib()
    server = attr.ib()
    ts = attr.ib()
    pts = attr.ib(default=None)

    @classmethod
    def from_dict(cls, kwargs):
        return cls(**kwargs)

    def to_dict(self):
        return attr.asdict(self)


def create_gathering(vk_api, message):
    all_ = True
    name = 'Кто на сходку?'
    args = message.text.split(' ')[1:]
    if args:
        name = ' '.join(args)

    if all_:
        chats, _ = vk_api.get_chat(message.chat_id())
        chat = chats[0]
        users = chat['users']
        names = ('@' + user['screen_name'] for user in users)
        text = '{} {}'.format(name, ' '.join(names))
        logger.info(vk_api.send(message.peer_id, text))


def handle_command(vk_api, message):
    command = message.text[1:].split(' ')[0].lower()
    if command == 'сходка':
        create_gathering(vk_api, message)


def message_handler(pool: queue.Queue, vk_api: VkApi):
    while True:
        message = pool.get()
        if message is None:
            break
        if not message.is_outbox():
            logger.info("Message '{}' from {}".format(message.text,
                                                      message.sender()))
            if message.is_chat() and message.text.startswith('/'):
                handle_command(vk_api, message)
            # poll1, _ = vk_api.create_poll('adf', '+', '-', '1')
            # poll2, _ = vk_api.create_poll('dw', '+', '-', '1')
            # attachment1 = Attachment('poll', poll1['owner_id'], poll1['id'])
            # attachment2 = Attachment('poll', poll2['owner_id'], poll2['id'])
            # logger.info(vk_api.send(message.peer_id,
            #                         None,
            #                         attachment1,
            #                         attachment2,
            #                         version='5.87'))

        pool.task_done()


@click.command()
@click.option('--access_token', envvar='ACCESS_TOKEN', required=True)
def main(access_token):
    access_token = access_token

    while True:
        try:
            vk_api = VkApi(access_token)
            long_poll_server, error = vk_api.get_long_poll_server()
            if error is not None:
                logger.warning(error)
                return

            messages_pool = queue.Queue(maxsize=100)

            message_handler_thread = threading.Thread(target=message_handler,
                                                      args=[messages_pool,
                                                            vk_api])
            message_handler_thread.start()

            while True:
                ts, updates = vk_api.get_long_poll_update(long_poll_server, 2)
                for update in updates:
                    if isinstance(update, MessageUpdate):
                        messages_pool.put(update)
                long_poll_server.ts = ts
        except Exception as e:
            logging.exception(e)


if __name__ == '__main__':
    main()

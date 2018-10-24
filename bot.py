import atexit
import copy
import threading
import os
import queue

import attr
import requests


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
        print(data)
        if code == 4:
            message_update = MessageUpdate(code=code,
                                           message_id=data[1],
                                           flags=data[2],
                                           peer_id=get(data, 3),
                                           timestamp=get(data, 4),
                                           text=get(data, 5),
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

    def is_chat(self):
        return self.peer_id > 2000000000

    def is_outbox(self):
        return self.flags & 2 == 2


class VkApi(object):
    BASE_VK_API_URL = 'https://api.vk.com/method/'

    def __init__(self, access_token):
        self._access_token = access_token

    def _make_request(self, method_name, params):
        url = f'{self.BASE_VK_API_URL}{method_name}'
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

    def get_long_poll_update(self, long_poll_server, mode=0):
        params = long_poll_server.to_dict()
        params.update({
            'act': 'a_check',
            'wait': 25,
            'mode': mode,
            'version': 2
        })
        url = f'https://{long_poll_server.server}'
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        return data['ts'], [Update.from_dict(u) for u in data['updates']]

    def send(self, peer_id, message, version='5.38'):
        method_name = 'messages.send'
        params = {
            'peer_id': peer_id,
            'message': message,
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


def message_handler(pool: queue.Queue, vkApi: VkApi):
    while True:
        message = pool.get()
        if message is None:
            break
        if not message.is_outbox():
            print(vkApi.send(message.peer_id, message.text))
        pool.task_done()


def main():
    access_token = os.environ['ACCESS_TOKEN']

    vkApi = VkApi(access_token)
    long_poll_server, error = vkApi.get_long_poll_server()
    if error is not None:
        print(error)
        return

    messages_pool = queue.Queue(maxsize=100)

    message_handler_thread = threading.Thread(target=message_handler,
                                              args=[messages_pool, vkApi])
    message_handler_thread.start()

    while True:
        ts, updates = vkApi.get_long_poll_update(long_poll_server, 0)
        for update in updates:
            if isinstance(update, MessageUpdate):
                messages_pool.put(update)
        long_poll_server.ts = ts


if __name__ == '__main__':
    main()

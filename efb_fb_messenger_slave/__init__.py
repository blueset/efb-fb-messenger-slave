import os
import pickle
from io import BytesIO
from typing import Optional, List, IO, Set, Dict, Any

import requests
import yaml
from fbchat import FBchatUserError, ThreadLocation
from fbchat.models import Thread
from ehforwarderbot import EFBChannel, EFBChat, EFBMsg, EFBStatus, ChannelType, MsgType
from ehforwarderbot import utils as efb_utils
from ehforwarderbot.utils import extra
from ehforwarderbot.status import EFBMessageRemoval
from ehforwarderbot.exceptions import EFBException, EFBOperationNotSupported

from .__version__ import __version__ as version
from .efms_chat import EFMSChat
from .efms_client import EFMSClient
from .master_messages import MasterMessageManager
from .extra_functions import ExtraFunctionsManager
from . import utils as efms_utils
from .utils import ExperimentalFlagsManager


class FBMessengerChannel(EFBChannel):
    channel_name: str = "Facebook Messenger Slave"
    channel_emoji: str = "⚡️"
    channel_id = "blueset.fbmessenger"
    channel_type: ChannelType = ChannelType.Slave
    __version__: str = version

    client: EFMSClient = None
    config: Dict[str, Any] = None

    def __init__(self):
        session_path = os.path.join(efb_utils.get_data_path(FBMessengerChannel.channel_id), "session.pickle")
        try:
            data = pickle.load(open(session_path, 'rb'))
            self.client = EFMSClient(None, None, session_cookies=data)
            self.client.channel = self
            EFMSChat.cache[EFBChat.SELF_ID] = EFMSChat(self,
                                                       self.client.fetchThreadInfo(self.client.uid)[self.client.uid])
        except FileNotFoundError:
            raise EFBException("Session not found, please authorize your account.\n"
                               "To do so, run:\n"
                               "     python3 -m " + self.__module__)
        except FBchatUserError as e:
            message = str(e) + "\n" + \
                      "You may need to re-authorize your account.\n" + \
                      "To do so, run:\n" + \
                      "     python3 -m " + self.__module__
            raise EFBException(message)

        self.load_config()
        self.flag: ExperimentalFlagsManager = ExperimentalFlagsManager(self)
        self.master_message: MasterMessageManager = MasterMessageManager(self)
        self.extra_functions: ExtraFunctionsManager = ExtraFunctionsManager(self)

        # Monkey patching
        Thread.__eq__ = lambda a, b: a.uid == b.uid

    def load_config(self):
        """
        Load configuration from path specified by the framework.

        Configuration file is in YAML format.
        """
        config_path = efb_utils.get_config_path(self.channel_id)
        if not os.path.exists(config_path):
            self.config: Dict[str, Any] = dict()
            return
        with open(config_path) as f:
            self.config: Dict[str, Any] = yaml.load(f)

    def get_chats(self) -> List[EFMSChat]:
        locations = (ThreadLocation.INBOX,)
        if self.flag('show_pending_threads'):
            locations += (ThreadLocation.PENDING, ThreadLocation.OTHER)
        if self.flag('show_archived_threads'):
            locations += (ThreadLocation.ARCHIVED,)
        chats = list(map(lambda graphql: EFMSChat(self, graph_ql_thread=graphql),
                         self.client.get_thread_list(location=locations)))
        loaded_chats = set(i.chat_uid for i in chats)
        for i in self.client.fetchAllUsers():
            if i.uid not in loaded_chats:
                chats.append(EFMSChat(self, thread=i))

        return chats

    def get_chat(self, chat_uid: str, member_uid: Optional[str] = None) -> EFMSChat:
        thread_id = member_uid or chat_uid
        if thread_id in EFMSChat.cache:
            chat = EFMSChat.cache[thread_id]
        else:
            # thread = self.client.fetchThreadInfo(thread_id)[str(thread_id)]
            # chat = EFMSChat(self, thread=thread)
            graph_ql = self.client.get_thread_info(thread_id)
            chat = EFMSChat(self, graph_ql_thread=graph_ql)
        if member_uid:
            chat.is_chat = False
            chat.group = self.get_chat(chat_uid)
        return chat

    def send_message(self, msg: EFBMsg) -> EFBMsg:
        return self.master_message.send_message(msg)

    def send_status(self, status: EFBStatus):
        if isinstance(status, EFBMessageRemoval):
            raise EFBOperationNotSupported("Messages cannot be removed in Facebook Messenger.")
        # Other status types go here
        raise EFBOperationNotSupported()

    def poll(self):
        self.client.listen()

    def stop_polling(self):
        self.client.listening = False

    def get_chat_picture(self, chat: EFBChat) -> IO[bytes]:
        photo_url = EFMSChat.cache[chat.chat_uid] and \
                    EFMSChat.cache[chat.chat_uid].vendor_specific.get('profile_picture_url')
        if not photo_url:
            thread = self.client.get_thread_info(chat.chat_uid)
            photo_url = efms_utils.get_value(thread, ('messaging_actor', 'big_image_src', 'uri'))
        if not photo_url:
            thread = self.client.fetchThreadInfo(chat.chat_uid)[chat.chat_uid]
            photo_url = getattr(thread, 'photo', None)
        if not photo_url:
            raise EFBOperationNotSupported('This chat has no picture.')
        photo = BytesIO(requests.get(photo_url).content)
        photo.seek(0)
        return photo

    # Extra functions

    @extra(name="Show threads list",
           desc="Usage:\n"
                "    {function_name}")
    def threads_list(self, args: str) -> str:
        return self.extra_functions.threads_list(args)

    @extra(name="Search for users",
           desc="Show the first 10 results."
                "Usage:\n"
                "    {function_name} keyword")
    def search_users(self, args: str) -> str:
        return self.extra_functions.search_users(args)

    @extra(name="Search for groups",
           desc="Show the first 10 results."
                "Usage:\n"
                "    {function_name} keyword")
    def search_groups(self, args: str) -> str:
        return self.extra_functions.search_groups(args)

    @extra(name="Search for pages",
           desc="Show the first 10 results."
                "Usage:\n"
                "    {function_name} keyword")
    def search_pages(self, args: str) -> str:
        return self.extra_functions.search_pages(args)

    @extra(name="Search for threads",
           desc="Show the first 10 results."
                "Usage:\n"
                "    {function_name} keyword")
    def search_threads(self, args: str) -> str:
        return self.extra_functions.search_threads(args)
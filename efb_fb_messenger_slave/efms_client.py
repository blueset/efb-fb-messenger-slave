import copy
import os
import urllib.parse
import logging
from typing import TYPE_CHECKING, Optional, Any, Dict, Tuple
from tempfile import NamedTemporaryFile

import requests
from fbchat import Client, ThreadType, FBchatException, logging, ThreadLocation, GraphQL
from fbchat.models import Message, EmojiSize
from fbchat.utils import check_request, get_jsmods_require, ReqUrl
from ehforwarderbot import EFBMsg, MsgType, coordinator
from ehforwarderbot.message import EFBMsgLinkAttribute, EFBMsgLocationAttribute

from .efms_chat import EFMSChat
from .utils import get_value


if TYPE_CHECKING:
    from . import FBMessengerChannel


class EFMSClient(Client):
    channel: "FBMessengerChannel" = None

    """Set of messages IDs sent by EFMS, popped when message is received again."""
    sent_messages = set()

    # Overrides for patches

    def __init__(self, *args, **kwargs):
        kwargs['logging_level'] = logging.root.level
        super().__init__(*args, **kwargs)
        self.logger = logging.getLogger(__name__)

    def fetchImageUrl(self, image_id):
        """Fetches the url to the original image from an image attachment ID
        Overrides original method.

        Args:
            image_id (str): The image you want to fetch

        Returns:
            str: An url where you can download the original image

        Raises:
            FBChatException: if request failed
        """
        image_id = str(image_id)
        j = check_request(self._get(ReqUrl.ATTACHMENT_PHOTO, query={'photo_id': str(image_id)}))

        url = get_jsmods_require(j, 3)
        if url is None:
            raise FBchatException('Could not fetch image url from: {}'.format(j))
        return url

    def send_image_file(self, image_path, mimetype, message=None, thread_id=None, thread_type=ThreadType.USER):
        """
        Sends a local image to a thread

        Args:
            image_path: Path of an image to upload and send
            mimetype: MIME type of the image
            message: Additional message
            thread_id: User/Group ID to send to. See :ref:`intro_threads`
            thread_type (models.ThreadType): See :ref:`intro_threads`

        Returns:
            :ref:`Message ID <intro_message_ids>` of the sent image

        Raises:
            FBchatException: if request failed
        """
        thread_id, thread_type = self._getThread(thread_id, thread_type)
        is_gif = (mimetype == 'image/gif')
        image_id = self._uploadImage(image_path, open(image_path, 'rb'), mimetype)
        return self.sendImage(image_id=image_id, message=message, thread_id=thread_id,
                              thread_type=thread_type, is_gif=is_gif)

    def markAsDelivered(self, thread_id, message_id):
        """Mark message as delivered"""
        super().markAsDelivered(thread_id, message_id)

    # Original methods

    def get_thread_list(self, limit: int=50, before: int=None,
                        location: Tuple[ThreadLocation, ...]=(ThreadLocation.INBOX,)):
        location = list(i.name for i in location)

        j = self.graphql_request(GraphQL(doc_id='1349387578499440', params={
            'limit': limit,
            'tags': location,
            'includeDeliveryReceipts': True,
            'includeSeqID': False,
            'before': before
        }))

        if j.get('viewer') is None:
            raise FBchatException('Could not fetch thread list: {}'.format(j))

        return j['viewer']['message_threads']['nodes']

    def get_thread_info(self, id: str):
        j = self.graphql_request(GraphQL(doc_id='1508526735892416', params={
            'id': id,
            'message_limit': 0,
            'load_message': 0,
            'load_read_receipt': False,
            'before': None,
        }))

        if j.get('message_thread') is None:
            raise FBchatException('Could not fetch thread list: {}'.format(j))

        return j['message_thread']

    def process_url(self, url: str) -> str:
        """Unwrap Facebook-proxied URL if necessary."""
        if self.client.flag('proxy_links_by_facebook'):
            return url
        if 'safe_image.php' in url:
            query = urllib.parse.urlparse(url).query
            escaped = urllib.parse.parse_qs(query).get('url', [url])[0]
            return escaped
        elif url.startswith('https://l.facebook.com/l.php'):
            query = urllib.parse.urlparse(url).query
            escaped = urllib.parse.parse_qs(query).get('u', [url])[0]
            return escaped
        else:
            return url

    def attach_media(self, msg: EFBMsg, attachment: Dict[str, Any]):
        """
        Attach an attachment to a message.

        Args:
            msg: Message to be attached to
            attachment:
                Dict of information of the attachment
                ``fbchat`` entity is not used as it is not completed.
        """
        blob_attachment: Dict[str, Any] = attachment.get('mercury', {}).get('blob_attachment', {})
        attachment_type: str = blob_attachment.get("__typename", None)
        if 'sticker_attachment' in attachment.get('mercury', {}):
            attachment_type = '__Sticker'
        if 'extensible_attachment' in attachment.get('mercury', {}):
            attachment_type = '__Link'
            if get_value(attachment, ('mercury', 'extensible_attachment',
                                      'story_attachment', 'target', '__typename')) == 'MessageLocation':
                attachment_type = 'MessageLocation'
        msg.filename = attachment.get('filename', None)
        msg.mime = attachment.get('mimeType', None)
        if attachment_type == "MessageAudio":
            msg.type = MsgType.Audio
            msg.filename = msg.filename or 'audio.mp4'
            msg.mime = msg.mime or 'audio/mpeg'
            ext = os.path.splitext(msg.filename)[1]
            msg.file = NamedTemporaryFile(suffix=ext)
            msg.file.write(requests.get(blob_attachment['playable_url']).content)
            msg.file.seek(0)
            msg.path = msg.file.name
        elif attachment_type == 'MessageImage':
            msg.type = MsgType.Image
            msg.filename = msg.filename or 'image.png'
            msg.mime = msg.mime or 'image/png'
            attribution_app = get_value(blob_attachment, ('attribution_app', 'name'))
            if attribution_app:
                if msg.text:
                    msg.text += " (via %s)" % attribution_app
                else:
                    msg.text = "via %s" % attribution_app
            ext = os.path.splitext(msg.filename)[1]
            msg.file = NamedTemporaryFile(suffix=ext)
            url = self.fetchImageUrl(attachment['id'])
            msg.file.write(requests.get(url).content)
            msg.file.seek(0)
            msg.path = msg.file.name
        elif attachment_type == 'MessageAnimatedImage':
            msg.type = MsgType.Image
            msg.filename = msg.filename or 'image.gif'
            msg.mime = msg.mime or 'image/gif'
            ext = os.path.splitext(msg.filename)[1]
            msg.file = NamedTemporaryFile(suffix=ext)
            msg.file.write(requests.get(blob_attachment['animated_image']['uri']).content)
            msg.file.seek(0)
            msg.path = msg.file.name
        elif attachment_type == 'MessageFile':
            msg.type = MsgType.Image
            msg.filename = msg.filename or 'file'
            msg.mime = msg.mime or 'application/octet-stream'
            ext = os.path.splitext(msg.filename)[1]
            msg.file = NamedTemporaryFile(suffix=ext)
            msg.file.write(requests.get(blob_attachment['url']).content)
            msg.file.seek(0)
            msg.path = msg.file.name
        elif attachment_type == 'MessageVideo':
            msg.type = MsgType.Image
            msg.filename = msg.filename or 'video.mp4'
            msg.mime = msg.mime or 'video/mpeg'
            ext = os.path.splitext(msg.filename)[1]
            msg.file = NamedTemporaryFile(suffix=ext)
            msg.file.write(requests.get(blob_attachment['playable_url']).content)
            msg.file.seek(0)
            msg.path = msg.file.name
        elif attachment_type == '__Sticker':
            if get_value(attachment, ('mercury', 'sticker_attachment', 'pack', 'id')) == "227877430692340":
                sticker_id = get_value(attachment, ('mercury', 'sticker_attachment', 'id'))
                for i in EmojiSize:
                    if sticker_id == i.value:
                        msg.type = MsgType.Text
                        msg.text = "👍 (%s)" % i.__name__[0]
                        return

            # TODO: Support animated sticker
            # 'msg': {'delta': {'attachments': [{'mercury': {'sticker_attachment': {
            #   'frame_count': 12,
            #   'frame_rate': 83,
            #   'frames_per_column': 3,
            #   'frames_per_row': 4,
            #   'height': 240,
            #   'id': '885398414828848',
            #   'label': '',
            #   'pack': {'id': '883825964986093'},
            #   'padded_sprite_image': {
            #       'uri': 'https://scontent-ams3-1.xx.fbcdn.net/v/t39.1997-6/s600x600/11891369_902338736468149_1431458554_n.png?_nc_ad=z-m&_nc_cid=0&oh=b7a78c140872691e9cd477aa62f8ed46&oe=5AF849A0'},
            #   'padded_sprite_image_2x': {
            #       'uri': 'https://scontent-ams3-1.xx.fbcdn.net/v/t39.1997-6/11891369_902338736468149_1431458554_n.png?_nc_ad=z-m&_nc_cid=0&oh=0223792dd23700d2b0f63308541aff0f&oe=5AB576DC'},
            #   'sprite_image': {
            #       'uri': 'https://scontent-ams3-1.xx.fbcdn.net/v/t39.1997-6/s480x480/11891363_902338733134816_126871890_n.png?_nc_ad=z-m&_nc_cid=0&oh=8af8f9ec52454f7a7ffd51ffae5c2f97&oe=5AF5EACD'},
            #   'sprite_image_2x': {
            #       'uri': 'https://scontent-ams3-1.xx.fbcdn.net/v/t39.1997-6/11891363_902338733134816_126871890_n.png?_nc_ad=z-m&_nc_cid=0&oh=f5f3844d37267c78fb9b68d91ef6e461&oe=5AF08F70'},
            #   'url': 'https://scontent-ams3-1.xx.fbcdn.net/v/t39.1997-6/11891357_902338729801483_2099101493_n.png?_nc_ad=z-m&_nc_cid=0&oh=b1c58300b9f87b8a1687ed8406b776a4&oe=5AB66910',
            #   'width': 240}}}],
            msg.type = MsgType.Sticker
            url = attachment['mercury']['sticker_attachment']['url']
            msg.text = attachment['mercury']['sticker_attachment'].get('label', '')
            msg.filename = os.path.split(urllib.parse.urlparse(url).path)[1]
            ext = os.path.splitext(msg.filename)[1]
            response = requests.get(url)
            msg.mime = response.headers['content-type']
            msg.file = NamedTemporaryFile(suffix=ext)
            msg.file.write(response.content)
            msg.file.seek(0)
            msg.path = msg.file.name
        elif attachment_type == '__Link':
            msg.type = MsgType.Link
            link_information = get_value(attachment, ('mercury', 'extensible_attachment', 'story_attachment'), {})
            title = get_value(link_information, ('title_with_entities', 'text'), '')
            description = get_value(link_information, ('description', 'text'), '')
            source = get_value(link_information, ('source', 'text'), None)
            if source:
                description += " (via %s)" % source
            preview = get_value(link_information, ('media', 'playable_url'), None) if \
                get_value(link_information, ('media', 'is_playable'), False) else None
            preview = preview or get_value(link_information, ('media', 'image', 'uri'), None)
            url = link_information['media']['url']
            msg.attributes = EFBMsgLinkAttribute(title=title,
                                                 description=description,
                                                 image=self.process_url(preview),
                                                 url=self.process_url(url))
        elif attachment_type == 'MessageLocation':
            msg.type = MsgType.Location
            link_information = get_value(attachment, ('mercury', 'extensible_attachment', 'story_attachment'), {})
            title = get_value(link_information, ('title_with_entities', 'text'), '')
            description = get_value(link_information, ('description', 'text'), '')
            msg.text = '\n'.join([title, description])
            preview = get_value(link_information, ('media', 'image', 'uri'), None)
            latitude, longitude = map(float. re.search(r'markers=([\d.]+)%2C([\d.]+)', preview).groups())
            msg.attributes = EFBMsgLocationAttribute(
                latitude=latitude, longitude=longitude
            )
        else:
            msg.type = MsgType.Unsupported
            msg.text = "Message type unsupported.\n%s" % msg.text

    def send(self, *args, **kwargs):
        result = super().send(*args, **kwargs)
        if result.startswith('mid.$'):
            self.sent_messages.add(result)
        return result

    def send_audio(self, file_id, message=None, thread_id=None, thread_type=ThreadType.USER):
        thread_id, thread_type = self._getThread(thread_id, thread_type)
        data = self._getSendData(message=message, thread_id=thread_id, thread_type=thread_type)

        data['action_type'] = 'ma-type:user-generated-message'
        data['has_attachment'] = True
        data['audio_ids[0]'] = file_id

        result = self._doSendRequest(data)
        if result.startswith('mid.$'):
            self.sent_messages.add(result)
        return result

    def send_file(self, file_id, message=None, thread_id=None, thread_type=ThreadType.USER):
        thread_id, thread_type = self._getThread(thread_id, thread_type)
        data = self._getSendData(message=message, thread_id=thread_id, thread_type=thread_type)

        data['action_type'] = 'ma-type:user-generated-message'
        data['has_attachment'] = True
        data['file_ids[0]'] = file_id

        result = self._doSendRequest(data)
        if result.startswith('mid.$'):
            self.sent_messages.add(result)
        return result

    def send_video(self, file_id, message=None, thread_id=None, thread_type=ThreadType.USER):
        thread_id, thread_type = self._getThread(thread_id, thread_type)
        data = self._getSendData(message=message, thread_id=thread_id, thread_type=thread_type)

        data['action_type'] = 'ma-type:user-generated-message'
        data['has_attachment'] = True
        data['video_ids[0]'] = file_id

        result = self._doSendRequest(data)
        if result.startswith('mid.$'):
            self.sent_messages.add(result)
        return result

    # Triggers

    def onMessage(self, mid: str=None, author_id: str=None, message: str=None, message_object: Message=None,
                  thread_id: str=None, thread_type: str=ThreadType.USER, ts: str=None, metadata=None, msg=None):
        """
        Called when the client is listening, and somebody sends a message

        Args:
            mid: The message ID
            author_id: The ID of the author
            message: (deprecated. Use `message_object.text` instead)
            message_object (models.Message): The message (As a `Message` object)
            thread_id: Thread ID that the message was sent to. See :ref:`intro_threads`
            thread_type (models.ThreadType): Type of thread that the message was sent to. See :ref:`intro_threads`
            ts: The timestamp of the message
            metadata: Extra metadata about the message
            msg: A full set of the data received
        """

        # Ignore messages sent by EFMS
        if mid in self.sent_messages:
            self.sent_messages.remove(mid)
            return

        efb_msg = EFBMsg()
        efb_msg.uid = mid
        efb_msg.text = message_object.text
        efb_msg.type = MsgType.Text
        efb_msg.deliver_to = coordinator.master

        # Authors
        efb_msg.author = EFMSChat(self.channel, uid=author_id)
        efb_msg.chat = EFMSChat(self.channel, uid=thread_id)

        if message_object.mentions:
            mentions = dict()
            for i in message_object.mentions:
                mentions[(i.offset, i.offset + i.length)] = EFMSChat(self.channel, uid=i.thread_id)

        if message_object.emoji_size:
            efb_msg.text += " (%s)" % message_object.emoji_size.name[0]
            # " (S)", " (M)", " (L)"

        attachments = msg.get('delta', {}).get('attachments', [])

        if len(attachments) > 1:
            for idx, i in enumerate(attachments):
                sub_msg = copy.copy(efb_msg)
                sub_msg.uid += ".%d" % idx
                self.attach_media(sub_msg, i)
                coordinator.send_message(efb_msg)
            return

        if attachments:
            self.attach_media(efb_msg, attachments[0])

        coordinator.send_message(efb_msg)

        self.markAsDelivered(mid, thread_id)
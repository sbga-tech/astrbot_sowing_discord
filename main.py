from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api.all import *
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from .core.forward_manager import ForwardManager
from .core.message_handler import MessageHandler
from .core.evaluation.evaluator import Evaluator
from .core.evaluation.rules import GoodEmojiRule
from .storage.local_cache import LocalCache
import asyncio

@register("astrbot_sowing_discord", "anka", "anka - 搬史插件", "1.0.0")
class Sowing_Discord(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.banshi_interval = config.get("banshi_interval", 30)
        self.banshi_group_list = config.get("banshi_group_list")
        self.banshi_target_list = config.get("banshi_target_list")
        self.block_source_messages = config.get("block_source_messages", False)
        self.local_cache = LocalCache()

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle_message(self, event:AstrMessageEvent):
        forward_manager = ForwardManager(event)
        evaluator = Evaluator(event)
        evaluator.add_rule(GoodEmojiRule())
        msg_id = event.message_obj.message_id

        if not self.banshi_target_list:
            self.banshi_target_list = await self.get_group_list(event)
        # 检查是否为转发消息
        raw_message = event.message_obj.raw_message
        message_list = raw_message.get("message") if isinstance(raw_message, dict) else None
        is_forward = (message_list is not None and 
                     isinstance(message_list, list) and 
                     message_list and
                     isinstance(message_list[0], dict) and
                     message_list[0].get("type") == "forward")
        
        if is_forward and event.message_obj.group_id in self.banshi_group_list:
            # 缓存消息进入本地
            await self.local_cache.add_cache(msg_id)
        # 检查本地中是否存在等待转发的消息
        waiting_messages = await self.local_cache.get_waiting_messages()
        if waiting_messages:
            for msg_id in waiting_messages:
                if await evaluator.evaluate(msg_id):
                    for target_id in self.banshi_target_list:
                        await forward_manager.send_forward_msg_raw(msg_id, target_id)
                        await asyncio.sleep(self.banshi_interval)
        
        if self.block_source_messages and event.message_obj.group_id in self.banshi_group_list:
            event.stop_event()
            return

    async def get_group_list(self, event: AstrMessageEvent):
        client = event.bot
        response = await client.api.call_action("get_group_list", **{"no_cache": False})
        group_ids = [item['group_id'] for item in response]
        return group_ids

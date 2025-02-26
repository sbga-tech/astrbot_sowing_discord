from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api.all import *
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent

@register("astrbot_sowing_discord", "anka", "anka - 搬史插件", "1.0.0")
class Sowing_Discord(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.banshi_group_list = config.get("banshi_group_list")
        self.banshi_target_list = config.get("banshi_target_list")


    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle_message(self, event:AstrMessageEvent):
        if not self.banshi_target_list:
            banshi_target_list = await self.get_group_list(event)
        if event.message_obj.raw_message["message"][0]["type"] == "forward" and event.message_obj.group_id in self.banshi_group_list:
            print(event.message_obj.raw_message["message"][0]["type"])
            client = event.bot
            shi_id = {
                "message_id": event.message_obj.message_id,
            }
            response = await client.api.call_action('get_forward_msg', **shi_id)
            forward_messages = response.get("messages")
            forward_nodes = []
            for msg in forward_messages:
                sender = msg.get("sender")
                content = msg.get("message")
                forward_nodes.append({
                    "type": "node",
                    "data": {
                        "user_id": sender["user_id"],
                        "nickname": sender["nickname"],
                        "content": content
                    }
                })
            for target_id in banshi_target_list:
                if target_id not in banshi_group_list:
                    await client.api.call_action(
                        "send_forward_msg",
                        group_id=target_id,
                        message=forward_nodes
                    )

    async def get_group_list(self, event: AstrMessageEvent):
        client = event.bot
        response = await client.api.call_action("get_group_list", **{"no_cache": False})
        group_ids = [item['group_id'] for item in response]
        return group_ids

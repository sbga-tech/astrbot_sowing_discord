from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api.all import *
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from astrbot.core.star.filter.platform_adapter_type import PlatformAdapterType
from .core.forward_manager import ForwardManager
from .core.message_handler import MessageHandler
from .core.evaluation.evaluator import Evaluator
from .core.evaluation.rules import GoodEmojiRule
from .storage.local_cache import LocalCache
import asyncio
import time # 引入 time 模块用于记录时间
from astrbot.api import logger

@register("astrbot_sowing_discord", "anka", "anka - 搬史插件", "1.0.0")
class Sowing_Discord(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        # 消息间的间隔，用于控制多条消息的发送频率
        self.banshi_interval = config.get("banshi_interval", 30) 
        self.banshi_group_list = config.get("banshi_group_list")
        self.banshi_target_list = config.get("banshi_target_list")
        self.block_source_messages = config.get("block_source_messages", False)
        self.local_cache = LocalCache()

    @filter.platform_adapter_type(PlatformAdapterType.AIOCQHTTP)
    async def handle_message(self, event:AstrMessageEvent):
        forward_manager = ForwardManager(event)
        evaluator = Evaluator(event)
        evaluator.add_rule(GoodEmojiRule())
        
        # 记录当前事件的msg_id和来源群
        source_group_id = event.message_obj.group_id
        msg_id = event.message_obj.message_id
        
        # --- (初始化和列表获取逻辑省略，与原代码相同) ---
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
        
        if is_forward and source_group_id in self.banshi_group_list:
            # 记录收到的转发消息和缓存动作
            logger.info(
                f"[SowingDiscord] 收到来自群 {source_group_id} 的转发消息 (ID: {msg_id})，已加入转发缓存。"
            )
            await self.local_cache.add_cache(msg_id)
        
        # --- 转发处理逻辑 (重点修改和日志输出) ---
        
        # 检查本地中是否存在等待转发的消息
        waiting_messages = await self.local_cache.get_waiting_messages()
        
        if waiting_messages:
            logger.info(
                f"[SowingDiscord] 检测到 {len(waiting_messages)} 条待转发消息，开始处理..."
            )
            
            # 循环处理每一条待转发的消息
            for index, msg_id_to_forward in enumerate(waiting_messages):
                current_time = time.time()
                
                # 评估消息是否符合转发规则
                if await evaluator.evaluate(msg_id_to_forward):
                    logger.info(
                        f"[SowingDiscord] **开始转发消息** (No.{index+1}, ID: {msg_id_to_forward})，目标群数量: {len(self.banshi_target_list)}。"
                    )
                    
                    # 循环转发给所有目标群
                    for target_id in self.banshi_target_list:
                        await forward_manager.send_forward_msg_raw(msg_id_to_forward, target_id)
                        logger.info(
                            f"[SowingDiscord] 成功转发消息 (ID: {msg_id_to_forward}) 到目标群: {target_id}。"
                        )
                        # 为了避免发送过于密集导致 API 问题，在目标群间也增加微小间隔
                        await asyncio.sleep(1) 
                        
                    # --- 核心修改：等待间隔移至此处 ---
                    logger.info(
                        f"[SowingDiscord] 消息 (ID: {msg_id_to_forward}) 完成多目标转发，进入 {self.banshi_interval} 秒冷却。"
                    )
                    
                    # 等待冷却时间，控制下一条消息的发送频率
                    await asyncio.sleep(self.banshi_interval)
                    
                    next_send_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(current_time + self.banshi_interval))
                    logger.info(
                        f"[SowingDiscord] 冷却结束，下一条消息转发将在 {next_send_time} 之后开始。"
                    )
                else:
                    logger.info(
                        f"[SowingDiscord] 消息 (ID: {msg_id_to_forward}) 评估未通过，跳过转发。"
                    )


        # --- (阻止原消息逻辑省略，与原代码相同) ---
        if self.block_source_messages and source_group_id in self.banshi_group_list:
            logger.info(
                f"[SowingDiscord] 阻止来源群 {source_group_id} 的原始消息显示。"
            )
            event.stop_event()
            return

    async def get_group_list(self, event: AstrMessageEvent):
        # ... (此方法未修改)
        client = event.bot
        response = await client.api.call_action("get_group_list", **{"no_cache": False})
        group_ids = [item['group_id'] for item in response]
        logger.info(
            f"[SowingDiscord] 目标群列表为空，自动获取到 {len(group_ids)} 个群组作为目标群。"
        )
        return group_ids
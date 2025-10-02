from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api.all import *
from astrbot.api import logger 
from aiocqhttp.exceptions import ActionFailed
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from astrbot.core.star.filter.platform_adapter_type import PlatformAdapterType
# 假设以下依赖都在同一目录的 .core 和 .storage 中
from .core.forward_manager import ForwardManager
# from .core.message_handler import MessageHandler # 未使用，可移除
from .core.evaluation.evaluator import Evaluator
from .core.evaluation.rules import GoodEmojiRule
from .storage.local_cache import LocalCache
import asyncio
import time
import uuid 

@register("astrbot_sowing_discord", "anka", "anka - 搬史插件", "1.0.0")
class Sowing_Discord(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.instance_id = str(uuid.uuid4())[:8] 
        
        self.banshi_interval = config.get("banshi_interval", 30)
        self.banshi_group_list = config.get("banshi_group_list")
        self.banshi_target_list = config.get("banshi_target_list")
        self.block_source_messages = config.get("block_source_messages", False)
        self.local_cache = LocalCache() 
        self.forward_lock = asyncio.Lock() 

        logger.info("=" * 60)
        logger.info(f"[SowingDiscord] 插件版本 1.0.0 已载入。实例ID: {self.instance_id}")
        logger.info(f"[SowingDiscord] 配置: banshi_interval (冷却时间): {self.banshi_interval} 秒")
        logger.info(f"[SowingDiscord] 配置: banshi_group_list (来源白名单): {self.banshi_group_list}")
        logger.info(f"[SowingDiscord] 配置: banshi_target_list (目标群列表): {self.banshi_target_list}")
        logger.info("=" * 60)

    @filter.platform_adapter_type(PlatformAdapterType.AIOCQHTTP)
    async def handle_message(self, event:AstrMessageEvent):
        forward_manager = ForwardManager(event)
        evaluator = Evaluator(event)
        evaluator.add_rule(GoodEmojiRule())
        
        source_group_id = event.message_obj.group_id
        msg_id = event.message_obj.message_id
        is_in_source_list = source_group_id in self.banshi_group_list
        
        logger.info(
            f"[SowingDiscord][ID:{self.instance_id}] 接收到消息事件，来源群ID: {source_group_id}。"
        )

        # 1. 确保目标群列表已加载
        if not self.banshi_target_list:
            self.banshi_target_list = await self.get_group_list(event)
            
        # 2. 检查是否为转发消息
        raw_message = event.message_obj.raw_message
        message_list = raw_message.get("message") if isinstance(raw_message, dict) else None
        is_forward = (message_list is not None and 
                    isinstance(message_list, list) and 
                    message_list and
                    isinstance(message_list[0], dict) and
                    message_list[0].get("type") == "forward")
        
        # 3. 缓存逻辑：仅对来自白名单的转发消息进行缓存。
        #    移除普通消息的提前退出逻辑，确保后续的转发检查能被执行。
        if is_forward and is_in_source_list:
            await self.local_cache.add_cache(msg_id)
            logger.info(
                f"[SowingDiscord][ID:{self.instance_id}] 任务：缓存。已缓存转发消息 (ID: {msg_id})。"
            )
        else:
             logger.info(
                f"[SowingDiscord][ID:{self.instance_id}] 任务：跳过缓存。当前消息 (ID: {msg_id}) 不满足缓存条件 (is_forward: {is_forward}, in_list: {is_in_source_list})."
            )

        # 4. 检查和转发等待超时的消息 (所有事件都会触发此检查)
        waiting_messages = await self.local_cache.get_waiting_messages()
        
        # 转发/冷却逻辑
        if waiting_messages:
            if self.forward_lock.locked():
                logger.warning(
                    f"[SowingDiscord][ID:{self.instance_id}] 任务：跳过转发。检测到 {len(waiting_messages)} 条待转发消息，但转发锁被占用。"
                )
                # 不返回，继续执行最后的阻止逻辑
            else:
                async with self.forward_lock:
                    logger.info(
                        f"[SowingDiscord][ID:{self.instance_id}] 执行任务：转发。成功获取转发锁。检测到 {len(waiting_messages)} 条待转发消息，开始处理..."
                    )
                    
                    for index, msg_id_to_forward in enumerate(waiting_messages):
                        start_time_for_cooldown = time.time()
                        target_list_str = ', '.join(map(str, self.banshi_target_list))
                        
                        try:
                            # 评估通过才进行转发
                            if await evaluator.evaluate(msg_id_to_forward):
                                logger.info(
                                    f"[SowingDiscord][ID:{self.instance_id}] 转发详情 (No.{index+1}, ID: {msg_id_to_forward})：目标群列表: [{target_list_str}]。"
                                )
                                
                                # 逐个目标群转发
                                for target_id in self.banshi_target_list:
                                    await forward_manager.send_forward_msg_raw(msg_id_to_forward, target_id)
                                    logger.info(
                                        f"[SowingDiscord][ID:{self.instance_id}] 发送日志：成功转发消息 (ID: {msg_id_to_forward}) 到目标群: {target_id}。"
                                    )
                                    await asyncio.sleep(1) # 在发送给不同目标之间添加短暂延迟
                                
                                # 转发成功后手动清除缓存
                                await self.local_cache.remove_cache(msg_id_to_forward)
                                logger.info(
                                    f"[SowingDiscord][ID:{self.instance_id}] 缓存清理：消息 (ID: {msg_id_to_forward}) 转发成功，已手动清除缓存。"
                                )

                                # **核心冷却逻辑**
                                logger.info(
                                    f"[SowingDiscord][ID:{self.instance_id}] 冷却开始：时长 {self.banshi_interval} 秒 (持有锁)。"
                                )
                                await asyncio.sleep(self.banshi_interval)
                                
                                end_time = time.time()
                                actual_duration = end_time - start_time_for_cooldown
                                logger.info(
                                    f"[SowingDiscord][ID:{self.instance_id}] 冷却结束：实际耗时约 {actual_duration:.2f} 秒 (包含发送时间)。"
                                )
                            else:
                                logger.info(
                                    f"[SowingDiscord][ID:{self.instance_id}] 消息 (ID: {msg_id_to_forward}) 评估未通过，跳过转发。"
                                )
                                # 评估失败后手动清除缓存
                                await self.local_cache.remove_cache(msg_id_to_forward)
                                logger.info(
                                    f"[SowingDiscord][ID:{self.instance_id}] 缓存清理：消息 (ID: {msg_id_to_forward}) 评估失败，已手动清除缓存。"
                                )
                                
                        except ActionFailed as e:
                            logger.error(
                                f"[SowingDiscord][ID:{self.instance_id}] 转发失败 (已捕获)：消息 ID {msg_id_to_forward} 评估或转发失败。原因: {e}"
                            )
                            # 异常失败后手动清除缓存
                            await self.local_cache.remove_cache(msg_id_to_forward)
                            logger.warning(
                                f"[SowingDiscord][ID:{self.instance_id}] 缓存清理：消息 (ID: {msg_id_to_forward}) 因异常失败，已手动清除缓存。继续下一条消息。"
                            )
                            continue

                logger.info(f"[SowingDiscord][ID:{self.instance_id}] 本次所有待转发消息处理完毕，释放转发锁。")
            
        # 5. 最终阻止/放行逻辑
        if self.block_source_messages and is_in_source_list and is_forward:
            # 只有当消息是来自白名单的转发消息，且设置了阻止时，才终止事件
            logger.info(
                f"[SowingDiscord][ID:{self.instance_id}] 任务：阻止。阻止来源群 {source_group_id} 的原始转发消息显示。"
            )
            event.stop_event()
            return
            
        # 6. 确保返回 None，让事件继续传递给其他插件
        return None

    async def get_group_list(self, event: AstrMessageEvent):
        client = event.bot
        response = await client.api.call_action("get_group_list", {"no_cache": False})
        group_ids = [item['group_id'] for item in response]
        logger.info(
            f"[SowingDiscord] 目标群列表为空，自动获取到 {len(group_ids)} 个群组作为目标群。"
        )
        return group_ids
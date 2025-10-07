from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api.all import *
from astrbot.api import logger 
from aiocqhttp.exceptions import ActionFailed
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from astrbot.core.star.filter.platform_adapter_type import PlatformAdapterType
from .core.forward_manager import ForwardManager
from .core.evaluation.evaluator import Evaluator
from .core.evaluation.rules import GoodEmojiRule
from .storage.local_cache import LocalCache
import asyncio
import time
import uuid 

SHARED_FORWARD_LOCK = asyncio.Lock()

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
        
        self.forward_lock = SHARED_FORWARD_LOCK 
        
        self._forward_task = None 

        # logger.info("=" * 60)
        # logger.info(f"[SowingDiscord] 插件版本 1.0.0 已载入。实例ID: {self.instance_id}")
        # logger.info(f"[SowingDiscord] 配置: banshi_interval (冷却时间): {self.banshi_interval} 秒")
        # logger.info(f"[SowingDiscord] 配置: banshi_group_list (来源白名单): {self.banshi_group_list}")
        # logger.info(f"[SowingDiscord] 配置: banshi_target_list (目标群列表): {self.banshi_target_list}")
        # logger.info("=" * 60)

    async def terminate(self):
        if self._forward_task and not self._forward_task.done():
            logger.warning(f"[SowingDiscord][ID:{self.instance_id}] 检测到正在冷却中的转发任务，正在尝试取消...")
            self._forward_task.cancel()
            try:
                await self._forward_task 
            except asyncio.CancelledError:
                logger.info(f"[SowingDiscord][ID:{self.instance_id}] 冷却任务已成功取消。")
            except Exception as e:
                logger.error(f"[SowingDiscord][ID:{self.instance_id}] 取消冷却任务时发生异常: {e}")

        if self.forward_lock.locked():
             logger.warning(f"[SowingDiscord][ID:{self.instance_id}] 警告：插件已卸载，但共享锁仍被占用。")
        
        logger.info(f"[SowingDiscord][ID:{self.instance_id}] 插件已卸载/重载。")
        
    @filter.platform_adapter_type(PlatformAdapterType.AIOCQHTTP)
    async def handle_message(self, event:AstrMessageEvent):
        forward_manager = ForwardManager(event)
        evaluator = Evaluator(event)
        evaluator.add_rule(GoodEmojiRule())
        
        source_group_id = event.message_obj.group_id
        msg_id = event.message_obj.message_id
        is_in_source_list = source_group_id in self.banshi_group_list
        
        sender_id = event.message_obj.self_id

        if not self.banshi_target_list:
            self.banshi_target_list = await self.get_group_list(event)
            
        raw_message = event.message_obj.raw_message
        message_list = raw_message.get("message") if isinstance(raw_message, dict) else None
        is_forward = (message_list is not None and 
                    isinstance(message_list, list) and 
                    message_list and
                    isinstance(message_list[0], dict) and
                    message_list[0].get("type") == "forward")
        
        if is_forward and is_in_source_list:
            await self.local_cache.add_cache(msg_id)
            logger.info(
                f"[SowingDiscord][ID:{self.instance_id}] 任务：缓存。已缓存转发消息 (ID: {msg_id}, 源头群: {source_group_id}, 发送者: {sender_id})。"
            )

        waiting_messages = await self.local_cache.get_waiting_messages()
        
        # 转发/冷却逻辑
        if waiting_messages:
            if self.forward_lock.locked():
                # logger.warning(
                #     f"[SowingDiscord][ID:{self.instance_id}] 任务：跳过转发。检测到 {len(waiting_messages)} 条待转发消息，但转发锁被占用。"
                # )
                pass
            else:
                self._forward_task = asyncio.create_task(
                    self._execute_forward_and_cool(event, forward_manager, evaluator, waiting_messages)
                )
                
        if self.block_source_messages and is_in_source_list:
            return MessageEventResult(None)
            
        return None

    @filter.platform_adapter_type(PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.ALL, priority=1)
    async def handle_recall_event(self, event: AstrMessageEvent):
        """
        检测到撤回后，立即从缓存中删除对应的消息ID，避免转发失败。
        """
        raw_message = event.message_obj.raw_message

        def get_value(obj, key, default=None):
            try:
                if isinstance(obj, dict):
                    return obj.get(key, default)
                return getattr(obj, key, default)
            except Exception:
                return default

        try:
            if event.message_obj.group_id not in self.banshi_group_list:
                return None 
            
            # 1. 识别事件类型
            post_type = get_value(raw_message, "post_type")
            
            if post_type == "notice":
                notice_type = get_value(raw_message, "notice_type")
                message_id = get_value(raw_message, "message_id")

                # 2. 检查是否为群/好友消息撤回事件
                if notice_type in ["group_recall", "friend_recall"] and message_id:
                    recalled_message_id = int(message_id)
                    logger.warning(
                        f"[SowingDiscord][ID:{self.instance_id}] 检测到消息撤回事件: {recalled_message_id}。"
                    )

                    # 3. 尝试从缓存中删除
                    if await self.local_cache.remove_cache(recalled_message_id):
                        logger.info(
                            f"[SowingDiscord][ID:{self.instance_id}] 缓存清理 (撤回)：消息 {recalled_message_id} 已从待转发缓存中移除，成功避免转发失败。"
                        )
                    else:
                        logger.debug(
                            f"[SowingDiscord][ID:{self.instance_id}] 缓存清理 (撤回)：消息 {recalled_message_id} 不在待转发缓存中 (可能已转发或过期)。"
                        )
                    
                    # 阻止此撤回事件继续传播
                    event.stop_event()
                    return MessageEventResult(None) #修改
        except Exception as e:
            logger.error(f"[SowingDiscord][ID:{self.instance_id}] 处理撤回事件时出现异常: {e}")
            pass

    async def _execute_forward_and_cool(self, event, forward_manager, evaluator, waiting_messages):
        try:
            async with self.forward_lock:
                logger.info(
                    f"[SowingDiscord][ID:{self.instance_id}] 执行任务：转发。成功获取转发锁。检测到 {len(waiting_messages)} 条待转发消息，开始处理..."
                )
                
                for index, msg_id_to_forward in enumerate(waiting_messages):
                    start_time_for_cooldown = time.time()
                    target_list_str = ', '.join(map(str, self.banshi_target_list))
                    
                    try:
                        if await evaluator.evaluate(msg_id_to_forward):
                            logger.info(
                                f"[SowingDiscord][ID:{self.instance_id}] 转发详情 (No.{index+1}, ID: {msg_id_to_forward})：目标群列表: [{target_list_str}]。"
                            )
                            
                            for target_id in self.banshi_target_list:
                                await forward_manager.send_forward_msg_raw(msg_id_to_forward, target_id)
                                logger.info(
                                    f"[SowingDiscord][ID:{self.instance_id}] 发送日志：成功转发消息 (ID: {msg_id_to_forward}) 到目标群: {target_id}。"
                                )
                                await asyncio.sleep(1) 
                            
                            await self.local_cache.remove_cache(msg_id_to_forward)
                            logger.info(
                                f"[SowingDiscord][ID:{self.instance_id}] 缓存清理：消息 (ID: {msg_id_to_forward}) 转发成功，已手动清除缓存。"
                            )

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
                            logger.info(f"[SowingDiscord][ID:{self.instance_id}] 消息 (ID: {msg_id_to_forward}) 评估未通过，跳过转发。")
                            await self.local_cache.remove_cache(msg_id_to_forward)
                            logger.info(f"[SowingDiscord][ID:{self.instance_id}] 缓存清理：消息 (ID: {msg_id_to_forward}) 评估失败，已手动清除缓存。")
                            
                    except ActionFailed as e:
                        logger.error(f"[SowingDiscord][ID:{self.instance_id}] 转发失败 (已捕获)：消息 ID {msg_id_to_forward} ... 原因: {e}")
                        await self.local_cache.remove_cache(msg_id_to_forward)
                        logger.warning(f"[SowingDiscord][ID:{self.instance_id}] 缓存清理：消息 (ID: {msg_id_to_forward}) 因异常失败，已手动清除缓存。继续下一条消息。")
                        continue
                    except asyncio.CancelledError:
                         logger.warning(f"[SowingDiscord][ID:{self.instance_id}] 任务在冷却期间被强制取消。")
                         raise

            logger.info(f"[SowingDiscord][ID:{self.instance_id}] 本次所有待转发消息处理完毕，释放转发锁。")
        except asyncio.CancelledError:
            logger.warning(f"[SowingDiscord][ID:{self.instance_id}] 转发任务协程被强制终止，确保锁已释放。")
        finally:
             self._forward_task = None

    async def get_group_list(self, event: AstrMessageEvent):
        client = event.bot
        response = await client.api.call_action("get_group_list", {"no_cache": False})
        group_ids = [item['group_id'] for item in response]
        logger.info(
            f"[SowingDiscord] 目标群列表为空，自动获取到 {len(group_ids)} 个群组作为目标群。"
        )
        return group_ids
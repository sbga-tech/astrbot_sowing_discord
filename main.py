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
        self.banshi_cache_seconds = config.get("banshi_cache_seconds", 3600)
        
        self.banshi_group_list = config.get("banshi_group_list")
        self.banshi_target_list = config.get("banshi_target_list")
        self.block_source_messages = config.get("block_source_messages", False)
        self.local_cache = LocalCache(max_age_seconds=self.banshi_cache_seconds) 
        
        self.forward_lock = SHARED_FORWARD_LOCK 
        self._forward_task = None 

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
        
        sender_id = event.get_sender_id()

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
                pass
            else:
                await self._execute_forward_and_cool(event, forward_manager, evaluator, waiting_messages)
                
        if self.block_source_messages and is_in_source_list:
            return MessageEventResult(None)
            
        return None

    async def _execute_forward_and_cool(self, event, forward_manager, evaluator, waiting_messages):
        """
        核心转发逻辑，包含对消息内容的预检查（是否被撤回/是否过期）。
        """
        # 1. 计算时间下限
        earliest_timestamp_limit = event.message_obj.timestamp - self.banshi_cache_seconds
        
        client = event.bot
        
        try:
            current_task = asyncio.current_task()
            
            async with self.forward_lock:
                logger.info(
                    f"[SowingDiscord][ID:{self.instance_id}] 执行任务：转发。成功获取转发锁。检测到 {len(waiting_messages)} 条待转发消息，开始处理..."
                )
                
                for index, msg_id_to_forward in enumerate(waiting_messages):
                    target_list_str = ', '.join(map(str, self.banshi_target_list))
                    
                    # === 2. 预检查：是否被撤回或过期 ===
                    try:
                        message_detail = await client.api.call_action("get_msg", message_id=int(msg_id_to_forward))
                        message_time = message_detail.get('time', 0)
                        msg_content = message_detail.get('message', [])
                        
                        # 检查 2.1: 消息时间是否超出缓存范围
                        if message_time < earliest_timestamp_limit:
                            logger.info(
                                f"[SowingDiscord] 预检查失败：消息ID {msg_id_to_forward} (时间: {time.strftime('%H:%M:%S', time.localtime(message_time))}) 已超出 {self.banshi_cache_seconds} 秒缓存限制。"
                            )
                            await self.local_cache.remove_cache(msg_id_to_forward)
                            continue 
                        
                        # 检查 2.2: 消息内容是否被撤回
                        if not msg_content:
                             logger.info(
                                f"[SowingDiscord] 预检查失败：消息ID {msg_id_to_forward} 内容为空或复杂类型，判断为已撤回/失效。"
                            )
                             await self.local_cache.remove_cache(msg_id_to_forward)
                             continue 
                        
                    except ActionFailed as e:
                        logger.info(
                            f"[SowingDiscord] 预检查API失败：消息ID {msg_id_to_forward} (可能已撤回/不存在)。原因: {e}"
                        )
                        await self.local_cache.remove_cache(msg_id_to_forward)
                        continue 
                        
                    # === 3. 预检查通过，开始转发流程 ===
                    start_time_for_cooldown = time.time()
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
                            logger.info(f"[SowingDiscord][ID:{self.instance_id}] 缓存清理：消息 (ID: {msg_id_to_forward}) 转发成功，已手动清除缓存。")

                            self._forward_task = current_task 
                            logger.info(f"[SowingDiscord][ID:{self.instance_id}] 冷却开始：时长 {self.banshi_interval} 秒 (持有锁)。")
                            await asyncio.sleep(self.banshi_interval)
                            self._forward_task = None # 冷却完成后清除跟踪
                            
                            end_time = time.time()
                            actual_duration = end_time - start_time_for_cooldown
                            logger.info(f"[SowingDiscord][ID:{self.instance_id}] 冷却结束：实际耗时约 {actual_duration:.2f} 秒 (包含发送时间)。")
                            
                        else:
                            logger.info(f"[SowingDiscord][ID:{self.instance_id}] 消息 (ID: {msg_id_to_forward}) 评估未通过，跳过转发。")
                            await self.local_cache.remove_cache(msg_id_to_forward)
                            logger.info(f"[SowingDiscord][ID:{self.instance_id}] 缓存清理：消息 (ID: {msg_id_to_forward}) 评估失败，已手动清除缓存。")
                            
                    except ActionFailed as e:
                        logger.error(f"[SowingDiscord][ID:{self.instance_id}] 转发失败 (API 拒绝)：消息 ID {msg_id_to_forward} ... 原因: {e}")
                        await self.local_cache.remove_cache(msg_id_to_forward)
                        logger.warning(f"[SowingDiscord][ID:{self.instance_id}] 缓存清理：消息 (ID: {msg_id_to_forward}) 因 API 拒绝而失败，已手动清除缓存。继续下一条消息。")
                        continue
                    except asyncio.CancelledError:
                         logger.warning(f"[SowingDiscord][ID:{self.instance_id}] 任务在冷却期间被强制取消。")
                         self._forward_task = None
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
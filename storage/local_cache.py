# 本地持久化缓存聊天记录id, 缓存后10分钟后查询是否满足条件, 满足条件后转发并删除缓存
from ..config import TEMP_DIR, WAITING_TIME
import os
import json
import time
from astrbot.api import logger 

class LocalCache:
    MAX_CACHE_AGE_SECONDS = 4 * 60 * 60 # 4 小时

    def __init__(self):
        self.cache_file = os.path.join(TEMP_DIR, "local_cache.json")
        self.WAITING_TIME = WAITING_TIME 
        cache_dir = os.path.dirname(self.cache_file)
        os.makedirs(cache_dir, exist_ok=True)
        
        if not os.path.exists(self.cache_file):
            with open(self.cache_file, "w") as f:
                json.dump({}, f)

    async def _cleanup_expired_cache(self):
        """清理缓存中超过 MAX_CACHE_AGE_SECONDS 的消息"""
        try:
            with open(self.cache_file, "r") as f:
                cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return 0 # 文件不存在或内容为空，无需清理

        current_time = time.time()
        keys_to_delete = []
        
        for message_id_str, timestamp in cache.items():
            if current_time - timestamp > self.MAX_CACHE_AGE_SECONDS:
                keys_to_delete.append(message_id_str)
        
        if keys_to_delete:
            for key in keys_to_delete:
                del cache[key]
            
            with open(self.cache_file, "w") as f:
                json.dump(cache, f)

        return len(keys_to_delete)

    async def add_cache(self, message_id: int):
        """添加一条message_id进入缓存, 保存时间"""
        str_message_id = str(message_id)
        with open(self.cache_file, "r") as f:
            cache = json.load(f)
        
        cache[str_message_id] = time.time()
        
        with open(self.cache_file, "w") as f:
            json.dump(cache, f)
    
    async def get_waiting_messages(self) -> list:
        """获取已经等待足够时间的消息列表，并首先进行过期清理"""
        
        # 1. 首先执行清理任务
        cleaned_count = await self._cleanup_expired_cache()
        if cleaned_count > 0:
            logger.info(f"[LocalCache] 清理了 {cleaned_count} 条过期缓存消息。")
        
        # 2. 获取待转发消息
        with open(self.cache_file, "r") as f:
            cache = json.load(f)
        
        waiting_messages = []
        current_time = time.time()
        
        for message_id_str, timestamp in cache.items():
            if current_time - timestamp > self.WAITING_TIME:
                waiting_messages.append(int(message_id_str))
        
        return waiting_messages
        
    async def remove_cache(self, message_id: int):
        """转发成功或失败后，手动删除指定的 message_id"""
        str_message_id = str(message_id)
        with open(self.cache_file, "r") as f:
            cache = json.load(f)
            
        if str_message_id in cache:
            del cache[str_message_id]
            with open(self.cache_file, "w") as f:
                json.dump(cache, f)
            return True
        return False
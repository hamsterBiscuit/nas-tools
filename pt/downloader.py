from datetime import datetime
from time import sleep

import log
from config import Config, PT_TAG
from message.send import Message
from pt.client.qbittorrent import Qbittorrent
from pt.client.transmission import Transmission
from pt.torrent import Torrent
from rmt.filetransfer import FileTransfer
from rmt.media import Media
from pt.media_server import MediaServer
from rmt.metainfo import MetaInfo
from utils.functions import str_timelong
from utils.types import MediaType, DownloaderType


class Downloader:
    client = None
    __client_type = None
    __seeding_time = None
    __pt_monitor_only = None
    message = None
    mediaserver = None
    filetransfer = None
    media = None

    def __init__(self):
        self.message = Message()
        self.mediaserver = MediaServer()
        self.filetransfer = FileTransfer()
        self.media = Media()
        self.init_config()

    def init_config(self):
        config = Config()
        pt = config.get_config('pt')
        if pt:
            pt_client = pt.get('pt_client')
            if pt_client == "qbittorrent":
                self.client = Qbittorrent()
                self.__client_type = DownloaderType.QB
            elif pt_client == "transmission":
                self.client = Transmission()
                self.__client_type = DownloaderType.TR
            self.__seeding_time = pt.get('pt_seeding_time')
            if self.__seeding_time:
                try:
                    self.__seeding_time = round(float(self.__seeding_time) * 24 * 3600)
                except Exception as e:
                    log.error("【PT】pt.pt_seeding_time 格式错误：%s" % str(e))
                    self.__seeding_time = None
            self.__pt_monitor_only = pt.get("pt_monitor_only")

    def add_pt_torrent(self, url, mtype=MediaType.MOVIE, is_paused=None, tag=None):
        """
        添加PT下载任务，根据当前使用的下载器分别调用不同的客户端处理
        :param url: 种子地址
        :param mtype: 媒体类型，电影、电视剧、动漫
        :param is_paused: 是否默认暂停，只有需要进行下一步控制时，才会添加种子时默认暂停
        :param tag: 下载时对种子的标记
        """
        ret = None
        if self.client:
            try:
                log.info("【PT】添加PT任务：%s" % url)
                if self.__client_type == DownloaderType.QB:
                    if self.__pt_monitor_only:
                        if not tag:
                            tag = PT_TAG
                        else:
                            tag = [PT_TAG, tag]
                    ret = self.client.add_torrent(url, mtype, is_paused=is_paused, tag=tag)
                else:
                    ret = self.client.add_torrent(url, mtype, is_paused=is_paused)
                    if ret and self.__pt_monitor_only:
                        self.client.set_torrent_tag(tid=ret.id, tag=PT_TAG)
            except Exception as e:
                log.error("【PT】添加PT任务出错：" + str(e))
        return ret

    def pt_transfer(self):
        """
        转移PT下载完成的文件，进行文件识别重命名到媒体库目录
        """
        if self.client:
            log.info("【PT】开始转移文件...")
            if self.__pt_monitor_only:
                tag = PT_TAG
            else:
                tag = None
            trans_tasks = self.client.get_transfer_task(tag=tag)
            for task in trans_tasks:
                done_flag, done_msg = self.filetransfer.transfer_media(in_from=self.__client_type,
                                                                       in_path=task.get("path"))
                if not done_flag:
                    log.warn("【PT】%s 转移失败：%s" % (task.get("path"), done_msg))
                self.client.set_torrents_status(task.get("id"))
            log.info("【PT】文件转移结束")

    def pt_removetorrents(self):
        """
        做种清理，保种时间为空或0时，不进行清理操作
        """
        if not self.client:
            return False
        # 空或0不处理
        if not self.__seeding_time:
            return
        if self.__pt_monitor_only:
            tag = PT_TAG
        else:
            tag = None
        log.info("【PT】开始执行PT做种清理，做种时间：%s..." % str_timelong(self.__seeding_time))
        torrents = self.client.get_remove_torrents(seeding_time=self.__seeding_time, tag=tag)
        for torrent in torrents:
            self.delete_torrents(torrent)
        log.info("【PT】PT做种清理完成")

    def pt_downloading_torrents(self):
        """
        查询正在下载中的种子信息
        :return: 客户端类型，下载中的种子信息列表
        """
        if not self.client:
            return []
        if self.__pt_monitor_only:
            tag = PT_TAG
        else:
            tag = None
        return self.__client_type, self.client.get_downloading_torrents(tag=tag)

    def get_torrents(self, torrent_ids):
        """
        根据ID或状态查询下载器中的种子信息
        :param torrent_ids: 种子ID列表
        :return: 客户端类型，种子信息列表
        """
        if not self.client:
            return None, []
        return self.__client_type, self.client.get_torrents(ids=torrent_ids)

    def start_torrents(self, ids):
        """
        下载控制：开始
        :param ids: 种子ID列表
        :return: 处理状态
        """
        if not self.client:
            return False
        return self.client.start_torrents(ids)

    def stop_torrents(self, ids):
        """
        下载控制：停止
        :param ids: 种子ID列表
        :return: 处理状态
        """
        if not self.client:
            return False
        return self.client.stop_torrents(ids)

    def delete_torrents(self, ids):
        """
        删除种子
        :param ids: 种子ID列表
        :return: 处理状态
        """
        if not self.client:
            return False
        return self.client.delete_torrents(delete_file=True, ids=ids)

    def get_pt_data(self):
        """
        获取PT下载软件中当前上传和下载量
        :return: 上传量、下载量
        """
        if not self.client:
            return 0, 0
        return self.client.get_pt_data()

    def check_and_add_pt(self, in_from, media_list, need_tvs=None):
        """
        根据命中的种子媒体信息，添加下载，由RSS或Searcher调用
        :param in_from: 来源
        :param media_list: 命中并已经识别好的媒体信息列表，包括名称、年份、季、集等信息
        :param need_tvs: 缺失的剧集清单，对于剧集只有在该清单中的季和集才会下载，对于电影无需输入该参数
        :return: 已经添加了下载的媒体信息表表、剩余未下载到的媒体信息
        """
        download_items = []
        # 返回按季、集数倒序排序的列表
        download_list = Torrent.get_download_list(media_list)
        # 电视剧整季匹配
        if need_tvs:
            # 先把整季缺失的拿出来，看是否刚好有所有季都满足的种子
            need_seasons = {}
            for need_title, need_tv in need_tvs.items():
                for tv in need_tv:
                    if not tv:
                        continue
                    if not tv.get("episodes"):
                        if not need_seasons.get(need_title):
                            need_seasons[need_title] = []
                        need_seasons[need_title].append(tv.get("season"))
            # 查找整季包含的种子，只处理整季没集的种子或者是集数超过季的种子
            for need_title, need_season in need_seasons.items():
                for item in download_list:
                    item_season = item.get_season_list()
                    item_episodes = item.get_episode_list()
                    if need_title == item.get_title_string() and item.type != MediaType.MOVIE and not item_episodes:
                        if set(item_season).issubset(set(need_season)):
                            download_items.append(item)
                            # 删除已下载的季
                            need_season = list(set(need_season).difference(set(item_season)))
                            for sea in item_season:
                                for tv in need_tvs.get(need_title):
                                    if sea == tv.get("season"):
                                        need_tvs[need_title].remove(tv)
                            if not need_tvs.get(need_title):
                                need_tvs.pop(need_title)
                                break
        # 电视剧季内的集匹配，也有可能没有找到整季
        if need_tvs:
            need_tv_list = list(need_tvs)
            for need_title in need_tv_list:
                need_tv = need_tvs.get(need_title)
                if not need_tv:
                    continue
                index = 0
                for tv in need_tv:
                    need_season = tv.get("season")
                    need_episodes = tv.get("episodes")
                    total_episodes = tv.get("total_episodes")
                    # 缺失整季的转化为缺失集进行比较
                    if not need_episodes:
                        need_episodes = list(range(1, total_episodes + 1))
                    for item in download_list:
                        if item.get_title_string() == need_title and item.type != MediaType.MOVIE:
                            item_season = item.get_season_list()
                            item_episodes = item.get_episode_list()
                            # 这里只处理单季含集的种子，从集最多的开始下
                            if len(item_season) == 1 and item_episodes and item_season[0] == need_season:
                                if set(item_episodes).issubset(set(need_episodes)):
                                    download_items.append(item)
                                    # 删除该季下已下载的集
                                    need_episodes = list(set(need_episodes).difference(set(item_episodes)))
                                    if need_episodes:
                                        need_tvs[need_title][index]["episodes"] = need_episodes
                                    else:
                                        need_tvs[need_title].pop(index)
                                        if not need_tvs.get(need_title):
                                            need_tvs.pop(need_title)
                                        break
                    index += 1

        # 处理所有电影
        for item in download_list:
            if item.type == MediaType.MOVIE:
                download_items.append(item)

        # 添加一遍PT任务
        return_items = []
        for item in download_items:
            log.info("【PT】添加PT任务：%s ..." % item.org_string)
            ret = self.add_pt_torrent(item.enclosure, item.type)
            if ret:
                if item not in return_items:
                    return_items.append(item)
                self.message.send_download_message(in_from, item)
            else:
                log.error("【PT】添加下载任务失败：%s" % item.get_title_string())
                self.message.sendmsg("添加PT任务失败：%s" % item.get_title_string())

        # 仍然缺失的剧集，从整季中选择需要的集数文件下载
        if need_tvs:
            need_tv_list = list(need_tvs)
            for need_title in need_tv_list:
                need_tv = need_tvs.get(need_title)
                if not need_tv:
                    continue
                index = 0
                for tv in need_tv:
                    need_season = tv.get("season")
                    need_episodes = tv.get("episodes")
                    if not need_episodes:
                        continue
                    for item in download_list:
                        if item in return_items:
                            continue
                        # 选中一个单季整季的
                        if item.get_title_string() == need_title \
                                and item.type != MediaType.MOVIE \
                                and not item.get_episode_list() \
                                and len(item.get_season_list()) == 1 \
                                and item.get_season_list()[0] == need_season:
                            log.info("【PT】添加PT任务并暂停：%s ..." % item.org_string)
                            torrent_tag = str(round(datetime.now().timestamp()))
                            ret = self.add_pt_torrent(url=item.enclosure, mtype=item.type, is_paused=True, tag=torrent_tag)
                            if ret:
                                return_items.append(item)
                            else:
                                log.error("【PT】添加下载任务失败：%s" % item.get_title_string())
                                self.message.sendmsg("添加PT任务失败：%s" % item.get_title_string())
                                continue
                            # 获取刚添加的任务ID
                            if self.__client_type == DownloaderType.TR:
                                if ret:
                                    torrent_id = ret.id
                                else:
                                    log.error("【PT】获取Transmission添加的种子信息出错：%s" % item.org_string)
                                    continue
                            else:
                                # 等待10秒，QB添加下载后需要时间
                                sleep(10)
                                torrent_id = self.client.get_last_add_torrentid_by_tag(torrent_tag)
                                if torrent_id is None:
                                    log.error("【PT】获取Qbittorrent添加的种子信息出错：%s" % item.org_string)
                                    continue
                                else:
                                    self.client.remove_torrents_tag(torrent_id, "NASTOOL")
                            # 设置任务只下载想要的文件
                            selected_episodes = self.set_files_status(torrent_id, need_episodes)
                            if not selected_episodes:
                                log.error("【PT】种子 %s 没有需要的集，删除下载任务..." % item.org_string)
                                self.client.delete_torrents(delete_file=True, ids=torrent_id)
                                continue
                            else:
                                log.info("【PT】%s 选取文件完成，选中集数：%s" % (item.org_string, len(selected_episodes)))
                            # 重新开始任务
                            log.info("【PT】%s 开始下载" % item.org_string)
                            self.start_torrents(torrent_id)
                            # 发送消息通知
                            self.message.send_download_message(in_from, item)
                            # 清除记忆并退出一层循环
                            need_episodes = list(set(need_episodes).difference(set(selected_episodes)))
                            if not need_episodes:
                                need_tvs[need_title].pop(index)
                                if not need_tvs.get(need_title):
                                    need_tvs.pop(need_title)
                                break
                            else:
                                need_tvs[need_title][index]["episodes"] = need_episodes
                index += 1

        # 返回下载的资源，剩下没下完的
        return return_items, need_tvs

    def check_exists_medias(self, meta_info, no_exists=None):
        """
        检查媒体库，查询是否存在，对于剧集同时返回不存在的季集信息
        :param meta_info: 已识别的媒体信息，包括标题、年份、季、集信息
        :param no_exists: 在调用该方法前已经存储的不存在的季集信息，有传入时该函数检索的内容将会叠加后输出
        :return: 当前媒体是否缺失，各标题总的季集和缺失的季集，需要发送的消息
        """
        if not no_exists:
            no_exists = {}
        # 查找的季
        if not meta_info.begin_season:
            search_season = None
        else:
            search_season = meta_info.get_season_list()
        # 查找的集
        search_episode = meta_info.get_episode_list()
        if search_episode and not search_season:
            search_season = [1]

        # 返回的消息列表
        message_list = []
        if meta_info.type != MediaType.MOVIE:
            # 是否存在的标志
            return_flag = False
            # 检索电视剧的信息
            tv_info = self.media.get_tmdb_tv_info(meta_info.tmdb_id)
            if tv_info:
                # 传入检查季
                total_seasons = []
                if search_season:
                    for season in search_season:
                        episode_num = self.media.get_tmdb_season_episodes_num(tv_info=tv_info, sea=season)
                        if not episode_num:
                            log.info("【PT】%s 第%s季 不存在" % (meta_info.get_title_string(), season))
                            message_list.append("%s 第%s季 不存在" % (meta_info.get_title_string(), season))
                            continue
                        total_seasons.append({"season_number": season, "episode_count": episode_num})
                        log.info("【PT】%s 第%s季 共有 %s 集" % (meta_info.get_title_string(), season, episode_num))
                else:
                    # 共有多少季，每季有多少季
                    total_seasons = self.media.get_tmdb_seasons_info(tv_info=tv_info)
                    log.info(
                        "【PT】%s %s 共有 %s 季" % (meta_info.type.value, meta_info.get_title_string(), len(total_seasons)))
                    message_list.append(
                        "%s %s 共有 %s 季" % (meta_info.type.value, meta_info.get_title_string(), len(total_seasons)))
                # 查询缺少多少集
                for season in total_seasons:
                    season_number = season.get("season_number")
                    episode_count = season.get("episode_count")
                    if not season_number or not episode_count:
                        continue
                    # 检查Emby
                    no_exists_episodes = self.mediaserver.get_no_exists_episodes(meta_info,
                                                                                 season_number,
                                                                                 episode_count)
                    # 没有配置Emby
                    if no_exists_episodes is None:
                        no_exists_episodes = self.filetransfer.get_no_exists_medias(meta_info,
                                                                                    season_number,
                                                                                    episode_count)
                    if no_exists_episodes:
                        # 排序
                        no_exists_episodes.sort()
                        # 缺失集初始化
                        if not no_exists.get(meta_info.get_title_string()):
                            no_exists[meta_info.get_title_string()] = []
                        # 缺失集提示文本
                        exists_tvs_str = "、".join(["%s" % tv for tv in no_exists_episodes])
                        # 存入总缺失集
                        if len(no_exists_episodes) >= episode_count:
                            no_item = {"season": season_number, "episodes": [], "total_episodes": episode_count}
                            log.info(
                                "【PT】%s 第%s季 缺失 %s 集" % (meta_info.get_title_string(), season_number, episode_count))
                            message_list.append("第%s季 缺失 %s 集" % (season_number, episode_count))
                        else:
                            no_item = {"season": season_number, "episodes": no_exists_episodes,
                                       "total_episodes": episode_count}
                            log.info(
                                "【PT】%s 第%s季 缺失集：%s" % (meta_info.get_title_string(), season_number, exists_tvs_str))
                            message_list.append("第%s季 缺失集：%s" % (season_number, exists_tvs_str))
                        if no_item not in no_exists.get(meta_info.get_title_string()):
                            no_exists[meta_info.get_title_string()].append(no_item)
                        # 输入检查集
                        if search_episode:
                            # 有集数，肯定只有一季
                            if not set(search_episode).intersection(set(no_exists_episodes)):
                                # 搜索的跟不存在的没有交集，说明都存在了
                                log.info("【PT】%s %s 在媒体库中已经存在" % (
                                    meta_info.get_title_string(), meta_info.get_season_episode_string()))
                                message_list.append("%s %s 在媒体库中已经存在" % (
                                    meta_info.get_title_string(), meta_info.get_season_episode_string()))
                                return_flag = True
                                break
                    else:
                        log.info(
                            "【PT】%s 第%s季 共%s集 已全部存在" % (meta_info.get_title_string(), season_number, episode_count))
                        message_list.append("第%s季 共%s集 已全部存在" % (season_number, episode_count))
            else:
                log.info("【PT】%s 无法查询到媒体详细信息" % meta_info.get_title_string())
                message_list.append("%s 无法查询到媒体详细信息" % meta_info.get_title_string())
                return_flag = None
            # 全部存在
            if return_flag is False and not no_exists.get(meta_info.get_title_string()):
                return_flag = True
            # 返回
            return return_flag, no_exists, message_list
        # 检查电影
        else:
            exists_movies = self.mediaserver.get_movies(meta_info.title, meta_info.year)
            if exists_movies is None:
                exists_movies = self.filetransfer.get_no_exists_medias(meta_info)
            if exists_movies:
                movies_str = "\n * ".join(["%s (%s)" % (m.get('title'), m.get('year')) for m in exists_movies])
                log.info("【PT】媒体库中已经存在以下电影：\n * %s" % movies_str)
                message_list.append("在媒体库中已经存在以下电影：\n * %s" % movies_str)
                return True, None, message_list
            return False, None, message_list

    def set_files_status(self, tid, need_episodes):
        """
        设置文件下载状态，选中需要下载的季集对应的文件下载，其余不下载
        :param tid: 种子的hash或id
        :param need_episodes: 需要下载的文件的集信息
        :return: 返回选中的集的列表
        """
        sucess_epidised = []
        if self.__client_type == DownloaderType.TR:
            files_info = {}
            torrent_files = self.client.get_files(tid)
            if not torrent_files:
                return []
            for file_id, torrent_file in enumerate(torrent_files):
                meta_info = MetaInfo(torrent_file.name)
                if not meta_info.get_episode_list():
                    selected = False
                else:
                    selected = set(meta_info.get_episode_list()).issubset(set(need_episodes))
                    if selected:
                        sucess_epidised = list(set(sucess_epidised).union(set(meta_info.get_episode_list())))
                if not files_info.get(tid):
                    files_info[tid] = {file_id: {'priority': 'normal', 'selected': selected}}
                else:
                    files_info[tid][file_id] = {'priority': 'normal', 'selected': selected}
            if sucess_epidised and files_info:
                self.client.set_files(files_info)
        elif self.__client_type == DownloaderType.QB:
            file_ids = []
            torrent_files = self.client.get_files(tid)
            if not torrent_files:
                return []
            for torrent_file in torrent_files:
                meta_info = MetaInfo(torrent_file.get("name"))
                if not meta_info.get_episode_list() or not set(meta_info.get_episode_list()).issubset(set(need_episodes)):
                    file_ids.append(torrent_file.get("index"))
                else:
                    sucess_epidised = list(set(sucess_epidised).union(set(meta_info.get_episode_list())))
            if sucess_epidised and file_ids:
                self.client.set_files(torrent_hash=tid, file_ids=file_ids, priority=0)
        return sucess_epidised

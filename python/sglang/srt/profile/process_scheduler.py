import multiprocessing
import logging
import math
# 创建一个logger
logger = logging.getLogger('profile')
logger.setLevel(logging.DEBUG)

# 创建一个handler，用于将日志写入文件
fh = logging.FileHandler('/data/home/josephyou/WXG_WORK/sglang/python/sglang/srt/profile/profile.log')
fh.setLevel(logging.DEBUG)


# 定义handler的输出格式
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
fh.setFormatter(formatter)

# 给logger添加handler
logger.addHandler(fh)


class ProcessScheduler:
    def __init__(self) -> None:
        # 创建一个Manager对象
        self.manager = multiprocessing.Manager()

        # 创建一个ProfileData实例
        self.shared_object = self.manager.Namespace()

        self.shared_object.mem_data = self.manager.list()
        self.shared_object.batch_data = self.manager.list()
        self.shared_object.type = self.manager.list()
        self.shared_object.max_size = 0.0

    
    def create_process(self, target, args):
        process = multiprocessing.Process(target=target, args=args)
        return process
    
    def start_process(self, process):
        process.start()

    def join_process(self, process):
        process.join()

    def get_shared_object(self):
        return self.shared_object
    

    def print_profile_data(self):
      

        # 记录一些日志

        logger.info(f"id\t\tmem\t\tbatch\t\ttype")
        length = min(len(self.shared_object.mem_data), len(self.shared_object.batch_data), len(self.shared_object.type))
        for i in range(0, length):
            logger.info(f"{i}\t\t{self.shared_object.mem_data[i]}\t\t{self.shared_object.batch_data[i]}\t\t{self.shared_object.type[i]}")
    

    def plot_profile_data(self):
        import matplotlib.pyplot as plt


        gpu_memory_usage_percentage = [(float(self.shared_object.max_size - usage) / self.shared_object.max_size) * 100 for usage in self.shared_object.mem_data]
        compute_resource_usage = [float(usage) for usage in self.shared_object.batch_data]

        fig, ax1 = plt.subplots(figsize=(10, 6))

        color = 'tab:red'
        ax1.set_xlabel('Steps')
        ax1.set_ylabel('GPU Memory Usage (%)', color=color)
        ax1.plot(gpu_memory_usage_percentage, color=color, label='GPU Memory Usage (%)')
        ax1.tick_params(axis='y', labelcolor=color)

        ax2 = ax1.twinx()  # 实例化一个共享相同x轴的第二个轴对象
        color = 'tab:blue'
        ax2.set_ylabel('Compute Resource Usage', color=color)  # 我们已经处理了y轴
        ax2.plot(compute_resource_usage, color=color, label='Compute Resource Usage')
        ax2.set_yscale('log')  # 设置y轴为对数比例
        ax2.tick_params(axis='y', labelcolor=color)

        fig.tight_layout()  # 为了确保两个y轴标签不会重叠
        ax1.legend(loc='upper left')
        ax2.legend(loc='upper right')
        plt.savefig('/data/home/josephyou/WXG_WORK/sglang/python/sglang/srt/profile/profile.png')
        
        plt.close()
        print("Memory use saved...")
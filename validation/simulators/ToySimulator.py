import torch
from torch.distributions import MultivariateNormal
import numpy as np

from validation.distributions.SeedableMultivariateNormal import SeedableMultivariateNormal
from validation.stresstests.CrossEntropyMethod import CrossEntropyMethod

class ToySimulator:
    def __init__(self, collision_threshold):
        self.position = torch.tensor([0.0, 0.0])
        self.collision_threshold = collision_threshold

    def reset(self):
        self.position = torch.tensor([0.0, 0.0])

    def step(self, noise):
        self.position += noise
        collision_value = -np.linalg.norm(self.position - torch.tensor([5.0, 5.0]))
        is_collision = np.linalg.norm(self.position) > self.collision_threshold
        return is_collision, collision_value, self.position

noise_seed = torch.Generator(device=torch.device)
q = SeedableMultivariateNormal(torch.zeros(2), torch.eye(2)*0.25, noise_seed=noise_seed)
p = SeedableMultivariateNormal(torch.zeros(2), torch.eye(2)*0.25, noise_seed=noise_seed)
collision_threshold = 10.0
goal_position = np.array([5.0, 5.0])

noise_mean = torch.zeros(12, 2)
noise_std = torch.ones(12, 2)

simulator = ToySimulator(collision_threshold)
cem = CrossEntropyMethod(simulator, q, p, 10, 3, 50, None, None, None)

means, covs, q, best_solutionMean, best_solutionCov, best_objective_value = cem.optimize()

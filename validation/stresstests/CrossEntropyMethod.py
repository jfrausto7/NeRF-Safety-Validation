import csv
import torch
import numpy as np
from scipy.special import logsumexp
from validation.distributions.SeedableMultivariateNormal import SeedableMultivariateNormal
from validation.simulators.NerfSimulator import NerfSimulator
from validation.utils.blenderUtils import runBlenderOnFailure
import seaborn as sns
import matplotlib.pyplot as plt

from validation.utils.mathUtils import is_positive_definite

class CrossEntropyMethod:
    def __init__(self, simulator, q, p, m, m_elite, kmax, noise_seed, blend_file, workspace, start_iter, start_k):
        """
        Initialize the CrossEntropyMethod class.

        Args:
            f: function to maximize. The simulator itself will return the value of the maximization function, so we don't need to pass it in.
            q: proposal distribution
            p: target distribution
            m: number of samples per iteration
            m_elite: number of elite samples per iteration
            kmax: number of populations
            noise_seed: noise seed generator used for SeedableMultivariateNormal
            blend_file: Blender file to use for visualizations
            workspace: Directory for NeRF intrinsics
        """
        self.steps = len(q.means)
        self.simulator = simulator
        self.q = q # 12 x 12 (12 step #s and 12 noise parameters)
        self.p = p # same as above
        self.m = m # 13?
        self.m_elite = m_elite # 12?
        self.kmax = kmax # 2?
        self.means = [0] * self.steps
        self.covs = [0] * self.steps
        self.collisions = 0
        self.stepsToCollision = 0
        self.blend_file = blend_file
        self.workspace = workspace
        self.noise_seed = noise_seed
        self.start_iter = start_iter
        self.start_k = start_k
        

        self.TOY_PROBLEM = False

    def optimize(self):
        """
        Perform the optimization process.

        Returns:
            mean: mean of the updated proposal distribution
            cov: covariance of the updated proposal distribution
            q: final proposal distribution
            best_solutionMean: best solution mean found during the optimization process
            best_solutionCov: best solution covariance matrix found during the optimization process
            best_objective_value: value of the function_to_maximize at the best_solution
        """

        populationScores = []
        eliteScores = []
        zeroedWeight = False # still unsure how this happens, but takes care of when the weights are all 0

        for k in range(self.start_k, self.kmax):
            print(f"Starting population {k}")
            # sample and evaluate function on samples
            population = [] # 10 x 12 x 12 array (one noise for every simulation)
            risks = np.array([])

            self.collisions = 0
            self.stepsToCollision = 0

            if self.TOY_PROBLEM:
                # plot the path of each simulation
                plt.figure()
            
            for simulationNumber in range(self.start_iter, self.m):
                # ONE SIMULATION BELOW
                self.simulator.reset()
                noises = self.q.sample(simulationNumber)
                trajectory = [noise.cpu().numpy() for noise in noises]
                outputSimulationList = []

                pCumulative = 0
                qCumulative = 0
                reward = 0

                if self.TOY_PROBLEM:
                    positions = np.array([[0, 0]], dtype=float)

                riskSteps = np.array([]) # store the score values for each step
                everCollided = False

                for stepNumber in range(self.steps):
                    outputStepList = [k, simulationNumber, stepNumber] # list written to the CSV
                    if isinstance(self.simulator, NerfSimulator):
                        isCollision, collisionVal, currentPos, sigma_d_opt, trace = self.simulator.step(noises[stepNumber])
                    else:
                        isCollision, collisionVal, currentPos = self.simulator.step(noises[stepNumber])

                    if self.TOY_PROBLEM:
                        # append the current position to positions
                        positions = np.append(positions, [currentPos], axis=0)

                    # append the noises
                    outputStepList.extend(trajectory[stepNumber])

                    if isinstance(self.simulator, NerfSimulator):
                        # calculate and handle reward/sigma
                        outputStepList.append(reward)
                        outputStepList.append(sigma_d_opt)
                        curLogLikelihood = self.p.distributions[stepNumber].log_prob(noises[stepNumber])
                        reward = self.simulator.reward(curLogLikelihood.cpu().numpy(), sigma_d_opt, trace)

                        # adjust risk
                        risk = collisionVal
                        scaling_factor = 0.01 * risk
                        scaled_reward = reward * scaling_factor
                        adjusted_risk = risk - scaled_reward
                        collisionVal = adjusted_risk

                    # append the sdf value and positions
                    outputStepList.append(collisionVal)
                    outputStepList.extend(currentPos)

                    # output the probability of the noise under p and q
                    pStep = self.p.distributions[stepNumber].log_prob(noises[stepNumber])
                    qStep = self.q.distributions[stepNumber].log_prob(noises[stepNumber])

                    pCumulative += pStep
                    qCumulative += qStep
                    outputStepList.append(pStep.item())
                    outputStepList.append(qStep.item())
                    outputStepList.append(pCumulative.item())
                    outputStepList.append(qCumulative.item())

                    # append the value of the step to the simulation data
                    outputSimulationList.append(outputStepList)

                    # output the collision value
                    outputStepList.append(isCollision)

                    # store sdf value
                    riskSteps = np.append(riskSteps, collisionVal)

                    # check for collisions
                    if isCollision:
                        self.collisions += 1
                        self.stepsToCollision += stepNumber
                        everCollided = True
                        if not self.TOY_PROBLEM:
                            runBlenderOnFailure(self.blend_file, self.workspace, simulationNumber, stepNumber, outputSimulationList, populationNum=k)
                        break
                
                if self.TOY_PROBLEM:
                    # plot the path of the simulation
                    plt.plot(positions[:, 0], positions[:, 1])                

                # store trajectory (for a simulation)
                population.append(trajectory)
                if self.TOY_PROBLEM:
                    risks = np.append(risks, riskSteps[-1]) # store the distance to the goal at the last step
                else:
                    risks = np.append(risks, min(riskSteps)) # store the smallest sdf value (the closest we get to a crash)
                
                # print the percentage of collisions and the average number of steps to collision, if a collision has occurred
                if everCollided:
                    print(f"Percentage of collisions: {self.collisions / (simulationNumber + 1) * 100}%")
                    print(f"Average number of steps to collision: {self.stepsToCollision / (self.collisions)}")

                    '''
                    Simulation data indexing for CrossEntropyMethod:
                    0: Population #
                    1: Simulation #
                    2: Step #
                    3-14: Noise data (12D)
                    15: Reward applied to this step
                    16: Uncertainty for this step
                    17: SDF value at position
                    18-20: XYZ Coordinates
                    21: Step trajectory likelihood under p
                    22: Step trajectory likelihood under q
                    23: Cumulative trajectory likelihood under p
                    24: Cumulative trajectory likelihood under q
                    25: Did we collide on this step
                    26: Did we collide on this simulation (added post facto)
                    '''

                if not self.TOY_PROBLEM:
                    # write results to CSV
                    with open(f"./results/collisionValuesCEM_m{self.m}melite{self.m_elite}k{self.kmax}.csv", "a") as csvFile:
                        print(f"Noise List: {trajectory}")
                        writer = csv.writer(csvFile)
                        for outputStepList in outputSimulationList:
                            outputStepList.append(everCollided)
                            writer.writerow(outputStepList) 

            if self.TOY_PROBLEM:
                # plot a star at the start and goal positions
                plt.plot(0, 0, 'ro')
                plt.plot(5, 5, 'go')
                plt.title(f"Simulation Paths for Population {k}")
                plt.savefig(f"./results/pltpaths/simulationPaths_pop{k}.png")
                plt.close()

            print(f"Average score of population {k}: {risks.mean()}")
            populationScores.append(risks.mean())

            # select elite samples and compute weights
            if self.TOY_PROBLEM:
                elite_indices = np.argsort(risks)[-self.m_elite:] # top m_elite indices
            else:
                elite_indices = np.argsort(risks)[:self.m_elite] # bottom m_elite indices
            elite_samples = torch.tensor(np.array(population)[elite_indices])

            # print average score of elite samples
            print(f"Average score of elite samples from population {k}: {risks[elite_indices].mean()}")
            eliteScores.append(risks[elite_indices].mean())
            weights = [0] * self.steps # each step in each elite sample carries a weight

            # compute the weights
            for i in range(self.steps):
                # compute the weights for the i-th step in each elite sample
                log_weights = (self.p.distributions[i].log_prob(elite_samples[:, i]) - self.q.distributions[i].log_prob(elite_samples[:, i])).cpu()

                # normalize the weights
                log_weights = log_weights - logsumexp(log_weights.cpu())

                print(f"Likelihood of step {i} of elite samples under p: {self.p.distributions[i].log_prob(elite_samples[:, i]).mean()}")
                weights[i] = torch.exp(log_weights).cuda()

                # check for negative/zero weights
                if torch.any(weights[i] <= 0):
                    print(f"Warning: Negative/zero weights detected: {weights[i]}")
                    weights[i] = torch.clamp(weights[i], min=1e-8)  # Set negative weights to barely above zero

                # update proposal distribution based on elite samples
                mean = weights[i] @ elite_samples[:, i] # (1 x 12) @ (12 x len(elite_samples)) = (1 x len(elite_samples))
                cov = torch.cov(elite_samples[:, i].T, aweights=weights[i])

                # only keep the diagonal of the covariance matrix 
                diag = cov.diag()
                if (diag > 0.1).any() or (diag < 0).any():
                    print(f"Step {i} in population {k} has a covariance matrix with a diagonal that is too large or negative! Clamping between 0 and 0.1...")
                    diag = torch.clamp(diag, 0, 0.1)
                
                cov = torch.diag(diag)
                self.means[i] = mean
                self.covs[i] = cov

                # check is cov is PD
                print("Covariance matrix is positive definite: " + str(is_positive_definite(cov)))
                plt.figure()
                for sample in population:
                    sns.histplot(sample[i], kde=True, bins=30)
                plt.title(f'Distribution of noise vectors at step {i}')
                plt.xlabel('Noise')
                plt.ylabel('Density')
                plt.savefig(f'./results/pltpaths/noise_distribution_step_{i}.png')
                plt.close()
            
            try:
                self.q = SeedableMultivariateNormal(self.means, self.covs, self.noise_seed)
            except ValueError:
                # may occur if q is improperly specified
                print(mean, cov)
                print(f"Highly improbable weights in population {k}! Exiting...")
                zeroedWeight = True
                break
            
            if zeroedWeight:
                break

            # print the updated proposal distribution
            print(f"Updated Proposal Distribution:")
            for i in range(self.steps):
                print(f"Step {i}: Mean: {self.means[i]}, Covariance: {self.covs[i]}")


        # plot the population and elite scores
        plt.figure()
        plt.plot(populationScores)
        plt.plot(eliteScores)
        plt.legend(["Population", "Elite"])

        # x and y labels
        plt.xlabel("Population #")
        plt.ylabel("Average Score")
        plt.savefig(f"./results/pltpaths/populationScores.png")
        plt.close()


        print("===FINISHED OPTIMIZATION===")
        print("===NOMINAL VALUES===\n")

        # print the nominal values
        for i in range(self.steps):
            print(f"Step {i}: Mean: {self.means[i]}, Covariance: {self.covs[i]}")

        # compute best solution and its objective value
        best_solutionMean, best_solutionCov, best_objective_value = self.q.compute_best_solution(self.simulator)
        
        return self.means, self.covs, self.q, best_solutionMean, best_solutionCov, best_objective_value
    
import csv
import torch
import numpy as np
from scipy.special import logsumexp
from validation.distributions.SeedableMultivariateNormal import SeedableMultivariateNormal
from validation.utils.blenderUtils import runBlenderOnFailure
import seaborn as sns
import matplotlib.pyplot as plt

import pdb

from validation.utils.mathUtils import is_positive_definite

class CrossEntropyMethod:
    def __init__(self, simulator, q, p, m, m_elite, kmax, noise_seed, blend_file, workspace):
        """
        Initialize the CrossEntropyMethod class.

        Args:
            f: function to maximize. The simulator itself will return the value of the maximization function, so we don't need to pass it in.
            q: proposal distribution
            p: target distribution
            m: number of samples per iteration
            m_elite: number of elite samples per iteration
            kmax: number of iterations
        """
        self.simulator = simulator
        self.q = q # 12 x 12 (12 step #s and 12 noise parameters)
        self.p = p # same as above
        self.m = m # 13?
        self.m_elite = m_elite # 12?
        self.kmax = kmax # 2?
        self.means = [0] * 12
        self.covs = [0] * 12
        self.collisions = 0
        self.stepsToCollision = 0
        self.blend_file = blend_file
        self.workspace = workspace
        self.noise_seed = noise_seed

        self.TOY_PROBLEM = False

    def optimize(self):
        """
        Perform the optimization process.

        Returns:
            mean: mean of the updated proposal distribution
            cov: covariance of the updated proposal distribution
            q: final proposal distribution
            best_solution: best solution found during the optimization process
            best_objective_value: value of the function_to_maximize at the best_solution
        """

        populationScores = []
        eliteScores = []
        zeroedWeight = False # still unsure how this happens, but takes care of when the weights are all 0

        for k in range(self.kmax):
            print(f"Starting population {k}")
            # sample and evaluate function on samples
            population = [] # 10 x 12 x 12 array (one noise for every simulation)
            risks = np.array([])

            self.collisions = 0
            self.stepsToCollision = 0

            if self.TOY_PROBLEM:
                # plot the path of each simulation
                plt.figure()
            
            for simulationNumber in range(self.m):
                self.simulator.reset()
                noises = self.q.sample()
                trajectory = [noise.cpu().numpy() for noise in noises]
                outputSimulationList = []

                pCumulative = 0
                qCumulative = 0


                if self.TOY_PROBLEM:
                    positions = np.array([[0, 0]], dtype=float)

                riskSteps = np.array([]) # store the score values for each step
                everCollided = False

                for stepNumber in range(12):
                    outputStepList = [k, simulationNumber, stepNumber] # what will be written to the CSV
                    isCollision, collisionVal, currentPos = self.simulator.step(noises[stepNumber])


                    if self.TOY_PROBLEM:
                        # append the current position to positions
                        positions = np.append(positions, [currentPos], axis=0)

                    # append the noises
                    outputStepList.extend(trajectory[stepNumber])
                    # append the sdf value and positions
                    outputStepList.append(collisionVal)
                    outputStepList.extend(currentPos)

                    # output the probability of the noise under p and q
                    pStep = self.p.log_prob(noises)[stepNumber]
                    qStep = self.q.log_prob(noises)[stepNumber]

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
                            runBlenderOnFailure(self.blend_file, self.workspace, simulationNumber, stepNumber, outputSimulationList)
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

                if not self.TOY_PROBLEM:
                    # write results to CSV using the format in MonteCarlo.py
                    with open("./results/collisionValuesCEM_k10m10.csv", "a") as csvFile:
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
            # elite_indices = np.argsort(risks)[-self.m_elite:] # top m_elite indices
            elite_indices = np.argsort(risks)[:self.m_elite] # bottom m_elite indices
            elite_samples = torch.tensor(np.array(population)[elite_indices])
            # print average score of elite samples
            print(f"Average score of elite samples from population {k}: {risks[elite_indices].mean()}")
            eliteScores.append(risks[elite_indices].mean())

            # weights = [0] * 12 # each step in each elite sample carries a weight

            # compute the weights
            weights = torch.exp(self.p.log_prob(elite_samples) - self.q.log_prob(elite_samples))
            # print(f"Likelihood of step {i} of elite samples under p: {self.p.log_prob(elite_samples)[i].mean()}")
            
            # normalize the weights
            log_weights = np.log(weights.cpu())
            weights = np.exp(log_weights - logsumexp(log_weights, dim=1)).cuda()

            # update proposal distribution based on elite samples
            means = weights @ elite_samples # (1 x 12) @ (12 x len(elite_samples)) = (1 x len(elite_samples))
            covs = torch.cov(elite_samples, rowvar=False, aweights=weights)

            # only keep the diagonal of the covariance matrix 
            diag = covs.diag()
            if (diag > 0.1).any() or (diag < 0).any():
                print(f"Covariance matrix has a diagonal that is too large or negative! Clamping between 0 and 0.1...")
                diag = torch.clamp(diag, 0, 0.1)

            covs = torch.diag(diag)

            self.means[i] = means
            self.covs[i] = covs

            for i in range(12):
                # check if covs are PD
                print("Covariance matrix is positive definite: " + str(is_positive_definite(covs[i])))
                plt.figure()
                for sample in population:
                    sns.histplot(sample[i], kde=True, bins=30)
                plt.title(f'Distribution of noise vectors at step {i}')
                plt.xlabel('Noise')
                plt.ylabel('Density')
                plt.savefig(f'./results/pltpaths/noise_distribution_step_{i}.png')
                plt.close()
            
            try:
                self.q = SeedableMultivariateNormal(means, covs, noise_seed=self.noise_seed)
            except ValueError:
                # may occur if f is improperly specified
                print(means, covs)
                print(f"Highly improbable weights! Exiting...")
                zeroedWeight = True
                break

            if zeroedWeight:
                break

            # print the updated proposal distribution
            print(f"Updated Proposal Distribution:")
            for i in range(12):
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
        for i in range(12):
            print(f"Step {i}: Mean: {self.means[i]}, Covariance: {self.covs[i]}")

        # compute best solution and its objective value
        best_objective_value = 999999
        self.simulator.reset()
        for stepNumber in range(12):
            best_solutionMean = self.means[i]
            best_solutionCov = self.covs[i]

            dist = SeedableMultivariateNormal(best_solutionMean, best_solutionCov, noise_seed=self.noise_seed)

            noise = dist.sample()
            print(f"Step {stepNumber} with noise: {noise}")
            isCollision, collisionVal, currentPos = self.simulator.step(noise)
            best_objective_value = min(best_objective_value, collisionVal)
            print(f"Collision: {isCollision}, Collision Value: {collisionVal}, Current Position: {currentPos}")

            if isCollision: break
        
        return self.means, self.covs, self.q, best_solutionMean, best_solutionCov, best_objective_value
    
